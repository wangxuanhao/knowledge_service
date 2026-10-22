# Windows bind 存储迁移至 Docker Linux named volumes

新版 `docker-compose.yml` 使用四个固定 named volumes。已有 bind 部署必须先停写、复制和校验，再切换 Compose。直接对空卷运行新版 Compose 会创建一套空数据库，无法代表旧数据已经迁移。

| 服务 | 旧目录 | 目标卷 | 容器内路径 |
| --- | --- | --- | --- |
| PostgreSQL | `volumes/postgres` | `knowledge_service_postgres_data` | `/var/lib/postgresql` |
| etcd | `volumes/etcd` | `knowledge_service_etcd_data` | `/etcd` |
| MinIO | `volumes/minio` | `knowledge_service_minio_data` | `/minio_data` |
| Milvus | `volumes/milvus` | `knowledge_service_milvus_data` | `/var/lib/milvus` |

此说明替代旧维护手册中“数据均在 bind 目录”的存储位置约定；其他数据库维护流程继续适用。迁移工具仅复制物理文件，不读取业务记录。源目录保留原状，工具不会停启服务、改写 Compose 或删除目录和卷。

## 1. 记录基线并停止全部写者

先记录数据库列表、各表行数、迁移版本/校验和，以及 Milvus collection 列表和逻辑行数；将业务名称和文件清单保存在私有审计目录。停止 API、索引任务、导入、测试和其他可能写入存储的程序，然后使用旧 Compose 停止四个服务。复制期间禁止重新启动它们。

下面为本机 PowerShell 示例，路径必须按实际部署设置；`$repo` 是旧 bind 部署目录，`$newCompose` 是待切换的新配置。

```powershell
$repo = 'D:\workspace\knowledge_service_v2\knowledge_service'
$pythonExe = 'D:\anaconda\envs\llm_model\python.exe'
$newCompose = 'C:\Users\Thinkpad\.codex\worktrees\knowledge-workspace-redesign\docker-compose.yml'
$audit = 'C:\private-audit\storage-migration-20261019'
New-Item -ItemType Directory -Path $audit -ErrorAction Stop
Copy-Item -LiteralPath "$repo\docker-compose.yml" -Destination "$audit\compose-bind-before.yml" -ErrorAction Stop
docker compose -f "$repo\docker-compose.yml" stop -t 60 milvus minio etcd postgres
```

检查四个容器均为 `exited`、退出码 `0`。非正常退出必须先调查并恢复干净停止状态。工具也会强制检查状态、原容器的 bind 路径与显式源目录一致。

## 2. 先检查，再复制

从包含迁移脚本的新 checkout 执行。默认模式只检查容器、源目录和目标卷；已有非空目标、已有挂载使用者、标签不匹配或错误源路径都会被拒绝。预检与执行使用不同的新报告路径。

```powershell
& $pythonExe scripts/migrate_docker_storage.py --source-root "$repo\volumes" --report "$audit\preflight.json" --windows-native
& $pythonExe scripts/migrate_docker_storage.py --source-root "$repo\volumes" --report "$audit\copy.json" --windows-native --execute --writers-stopped
if ($LASTEXITCODE -ne 0) { throw 'Storage copy failed; keep services stopped and inspect the audit report.' }
```

Windows 推荐 `--windows-native`：宿主单次遍历，在写入 PAX 归档的同一遍读取中计算逐文件 SHA-256；通过 Win32 API 读取 NTFS 的 `$LXUID`、`$LXGID`、`$LXMOD`，保留 Docker bind 视图的 Linux 属主和权限。mtime 用 PAX 保存纳秒。Linux 解压后只读回读目标，逐项比较相对路径、类型、字节数、SHA-256、uid、gid、mode、纳秒 mtime。遇到 symlink/reparse point 会拒绝，需单独制定迁移方案。没有 WSL 元数据的路径按 Docker Desktop 默认 `0:0 / 0777` 处理，并以目标回读核对。

其他宿主可省略 `--windows-native`，使用 `copy_stopped_docker_volume.sh` 在 Linux helper 中归档只读源，再以目标归档哈希、文件数、字节数核对。该 GNU 格式路径的 mtime 精度为秒；不迁移 ACL、任意 xattr、稀疏布局或外部硬链接关系。需要这些属性的部署应先调整迁移方案。

helper 镜像默认为本机已安装的 `milvusdb/milvus:v3.0.2`，通过 `--pull never` 和 `--network none` 运行。归档与清单包含存储文件及路径，必须保存在私有目录并按备份管理；工具不会自动清理它们。预留最大单卷归档与目标数据所需空间。

如果中途中断，已经写入的目标会被视为非空并拒绝重跑。保留报告和目标以调查；不要为了重试直接清空或覆盖目标。脚本没有自动恢复、删除或强制覆盖开关。

## 3. 全部校验通过后切换并恢复

确认执行报告中 `all_copies_verified` 为 `true`，再切换配置。`.env` 与 `docker/postgres/initdb` 继续使用旧部署目录。配置检查使用 `--quiet`，避免输出凭证。

```powershell
Copy-Item -LiteralPath $newCompose -Destination "$repo\docker-compose.yml" -ErrorAction Stop
docker compose -f "$repo\docker-compose.yml" config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Compose validation failed; keep services stopped.' }
docker compose -f "$repo\docker-compose.yml" up -d --wait --wait-timeout 300 postgres etcd minio milvus
```

检查健康状态及四个容器的数据挂载类型均为 `volume`，名称与上表一致。对照停机前基线核验数据库列表、所有表计数、迁移校验和、Milvus collection 名称及逻辑行数。Milvus 恢复/compaction 可改变物理统计，不能据此单独判断丢失逻辑数据。完成核验后才启动应用和索引任务；持续观察 Milvus 健康与 restart count。

## 4. 回退边界

旧 `volumes` 目录是停机时刻的离线快照。切换之后的新写入只进入 named volumes，旧目录不会同步更新。恢复写入前，如恢复核验失败，可保持全部写者停止、停止新存储服务、恢复备份的 bind Compose，然后启动旧服务核验。

恢复写入后不能直接切回旧目录，否则会看不到迁移之后的写入；必须另做反向停写迁移或从逻辑备份恢复。不要使用 `docker compose down -v`，它会删除当前真实数据卷。原目录、归档和目标卷的清理需另行明确安排。
