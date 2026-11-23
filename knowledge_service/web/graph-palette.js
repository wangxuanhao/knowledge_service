/* 同一工作台内按规范类型 ID 分配颜色。局部图、全量图和筛选共用分配结果。 */
(function () {
  var PALETTE = [
    '#376bb0', '#4f9741', '#ad8900', '#cc4254',
    '#16849e', '#087052', '#d2671b', '#8e5ab5',
    '#b12b82', '#865435', '#377873', '#66519c',
    '#75891d', '#46546a', '#0987d0', '#8d2940'
  ];
  var ATTRIBUTE = '#8c7b68';
  var UNKNOWN = '#9aa8a1';

  var cache = new Map();
  var used = new Set([ATTRIBUTE, UNKNOWN]);

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
    if (type === '属性值') return ATTRIBUTE;
    return entityColorFor(type);
  }

  function entityColorFor(type) {
    if (!type) return UNKNOWN;
    var key = String(type);
    if (cache.has(key)) return cache.get(key);
    var seed = hash(key);
    var start = seed % PALETTE.length;
    var color;
    for (var step = 0; step < PALETTE.length; step += 1) {
      var candidate = PALETTE[(start + step) % PALETTE.length];
      if (!used.has(candidate)) {
        color = candidate;
        break;
      }
    }
    // 色板用完后继续分配中深色，逐一避开已用色和保留色。
    while (!color) {
      var channels = [14, 7, 0].map(function (shift) {
        return (40 + ((seed >>> shift) & 127)).toString(16).padStart(2, '0');
      });
      var extra = '#' + channels.join('');
      if (!used.has(extra)) color = extra;
      seed += 0x9e3779;
    }
    used.add(color);
    cache.set(key, color);
    return color;
  }

  function colorMap(types) {
    var map = new Map();
    Array.from(new Set(types || [])).filter(function (type) {
      return type && type !== '属性值';
    }).sort().forEach(function (type) {
      map.set(type, colorFor(type));
    });
    return map;
  }

  function legendFor(types) {
    return (types || []).map(function (name) {
      return { name: name, itemStyle: { color: colorFor(name) } };
    });
  }

  window.GraphPalette = {
    palette: PALETTE,
    colorFor: colorFor,
    entityColorFor: entityColorFor,
    colorMap: colorMap,
    legendFor: legendFor,
    attribute: ATTRIBUTE,
    unknown: UNKNOWN
  };
})();
