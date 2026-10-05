/* 类型 → 颜色的唯一出处。
 *
 * 为什么需要它：以前图谱颜色是"这个类型在数组里的下标"决定的（palette[index % 9]），
 * 于是有两个必然的错：
 *   ① 同一个实体类型，换个加载顺序/换个检索结果就换颜色，跨页面更对不上；
 *   ② "按实体类型查看"那排圆点用的是按钮下标 i，而第 0 项是「全部类型」——
 *      整体错位一格，圆点颜色正好等于图里"下一个类型"的颜色。
 *      用户反馈就是这个："节点底下类型颜色和实体类型颜色都不统一"。
 * 现在按**类型名哈希**取色：同名必同色，与顺序、与页面、与结果集无关。
 *
 * 另有两个专用色，不参与哈希：
 *   属性值（attribute）——它不是实体类型，固定一色，免得和实体类型混在一起看；
 *   空/未知类型——中性灰。
 */
(function () {
  // 16 色：色相分散、浅底上对比够；刻意避开纯红/纯绿相邻，减少色盲混淆。
  var PALETTE = [
    '#5470c6', '#91cc75', '#fac858', '#ee6666',
    '#73c0de', '#3ba272', '#fc8452', '#9a60b4',
    '#ea7ccc', '#c47f3b', '#4f9aa8', '#b25c9b',
    '#7a8f3a', '#8c6d4f', '#5f7fc4', '#c0554f'
  ];
  var ATTRIBUTE = '#8c7b68';   // 属性值（不是实体类型）
  var UNKNOWN = '#9aa8a1';     // 空类型/未知类型

  var cache = new Map();

  // FNV-1a：短字符串分布够均匀，且不依赖语言环境（Java 的 hashCode 之类不行）
  function hash(text) {
    var value = 2166136261;
    for (var i = 0; i < text.length; i += 1) {
      value ^= text.charCodeAt(i);
      value = Math.imul(value, 16777619);
    }
    return value >>> 0;
  }

  function colorFor(type) {
    if (!type) return UNKNOWN;
    if (type === '属性值') return ATTRIBUTE;
    var key = String(type);
    if (cache.has(key)) return cache.get(key);
    var color = PALETTE[hash(key) % PALETTE.length];
    cache.set(key, color);
    return color;
  }

  /* 同一张图里两个类型撞到同一色太常见了（16 色放 8 个类型，撞色概率 80%+），
   * 撞了用户会以为是 bug。所以再给一层"同一视图内不重色"的兜底：
   * 把视图里的类型名排序后依次取色，默认色被占了就往后挪一格。
   * 排序保证：**同一批类型在任何页面/任何顺序下都得到同一组颜色**，
   * 而单类型查询仍用 colorFor（名字哈希），所以过滤后的单类型颜色不会乱跳。
   * 保留色（属性值/空）不参与挪位，它们本来就不在调色板里。 */
  function colorMap(types) {
    var sorted = (types || []).filter(function (t) { return t && t !== '属性值'; }).slice().sort();
    var used = new Set();
    var map = new Map();
    sorted.forEach(function (type) {
      var color = colorFor(type);
      if (used.has(color)) {
        var start = PALETTE.indexOf(color);
        for (var step = 1; step <= PALETTE.length; step += 1) {
          var candidate = PALETTE[(start + step) % PALETTE.length];
          if (!used.has(candidate)) { color = candidate; break; }
        }
      }
      used.add(color);
      map.set(type, color);
    });
    return map;
  }

  // 一小组类型的图例（顺序无所谓，颜色由类型名决定）
  function legendFor(types) {
    return (types || []).map(function (name) {
      return { name: name, itemStyle: { color: colorFor(name) } };
    });
  }

  window.GraphPalette = {
    palette: PALETTE,
    colorFor: colorFor,
    colorMap: colorMap,
    legendFor: legendFor,
    attribute: ATTRIBUTE,
    unknown: UNKNOWN
  };
})();
