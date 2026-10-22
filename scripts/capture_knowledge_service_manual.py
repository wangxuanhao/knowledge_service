"""旧 v1.1.0 自动截图流程已停用。

旧流程包含默认本体与旧页面操作，不能用于现行统一写入流程的验收。
当前手册截图应在真实运行应用中按本版操作故事获取，保存到
docs/manual-redesign/，并记录项目、对象、页面及操作回执。
此入口不登录、不创建账号/项目、不读取凭据，也不更改已有截图。
"""


def main() -> int:
    print('旧 v1.1.0 截图流程已停用。请在运行应用核对三条写入路径，'
          '将真实页面截图保存到 docs/manual-redesign/；'
          '手册构建使用 python scripts/verify_redesign_manual.py --build。')
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
