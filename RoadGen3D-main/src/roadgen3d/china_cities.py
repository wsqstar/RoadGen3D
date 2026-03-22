"""Registry of major Chinese cities with representative bounding boxes for OSM data.

Each city has a single ~500m x 500m bbox covering a representative commercial /
mixed-use street block in its urban core.  The bbox format is
``(min_lon, min_lat, max_lon, max_lat)`` in WGS-84.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class CityRecord:
    """A single city entry in the registry."""

    name_zh: str  # Chinese name, e.g. "北京"
    name_en: str  # Lowercase English identifier, e.g. "beijing"
    province: str  # Province / municipality, e.g. "北京市"
    bbox: Tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)


# ---------------------------------------------------------------------------
# City registry (~80 major cities)
# ---------------------------------------------------------------------------

CHINA_CITY_REGISTRY: Tuple[CityRecord, ...] = (
    # ---- 直辖市 (4) ----
    CityRecord("北京", "beijing", "北京市", (116.3970, 39.9130, 116.4020, 39.9175)),
    CityRecord("上海", "shanghai", "上海市", (121.4690, 31.2280, 121.4740, 31.2325)),
    CityRecord("天津", "tianjin", "天津市", (117.1940, 39.1230, 117.1990, 39.1275)),
    CityRecord("重庆", "chongqing", "重庆市", (106.5780, 29.5570, 106.5830, 29.5615)),
    # ---- 省会 / 自治区首府 (27) ----
    CityRecord("石家庄", "shijiazhuang", "河北省", (114.5100, 38.0410, 114.5150, 38.0455)),
    CityRecord("太原", "taiyuan", "山西省", (112.5490, 37.8680, 112.5540, 37.8725)),
    CityRecord("呼和浩特", "hohhot", "内蒙古", (111.7490, 40.8390, 111.7540, 40.8435)),
    CityRecord("沈阳", "shenyang", "辽宁省", (123.4290, 41.7960, 123.4340, 41.8005)),
    CityRecord("长春", "changchun", "吉林省", (125.3190, 43.8870, 125.3240, 43.8915)),
    CityRecord("哈尔滨", "harbin", "黑龙江省", (126.6340, 45.7560, 126.6390, 45.7605)),
    CityRecord("南京", "nanjing", "江苏省", (118.7870, 32.0410, 118.7920, 32.0455)),
    CityRecord("杭州", "hangzhou", "浙江省", (120.1530, 30.2590, 120.1580, 30.2635)),
    CityRecord("合肥", "hefei", "安徽省", (117.2770, 31.8570, 117.2820, 31.8615)),
    CityRecord("福州", "fuzhou", "福建省", (119.2960, 26.0750, 119.3010, 26.0795)),
    CityRecord("南昌", "nanchang", "江西省", (115.8920, 28.6780, 115.8970, 28.6825)),
    CityRecord("济南", "jinan", "山东省", (117.0050, 36.6630, 117.0100, 36.6675)),
    CityRecord("郑州", "zhengzhou", "河南省", (113.6510, 34.7460, 113.6560, 34.7505)),
    CityRecord("武汉", "wuhan", "湖北省", (114.2720, 30.5690, 114.2770, 30.5735)),
    CityRecord("长沙", "changsha", "湖南省", (112.9770, 28.1960, 112.9820, 28.2005)),
    CityRecord("广州", "guangzhou", "广东省", (113.2660, 23.1280, 113.2710, 23.1325)),
    CityRecord("南宁", "nanning", "广西", (108.3630, 22.8120, 108.3680, 22.8165)),
    CityRecord("海口", "haikou", "海南省", (110.3440, 20.0200, 110.3490, 20.0245)),
    CityRecord("成都", "chengdu", "四川省", (104.0650, 30.6590, 104.0700, 30.6635)),
    CityRecord("贵阳", "guiyang", "贵州省", (106.7090, 26.6450, 106.7140, 26.6495)),
    CityRecord("昆明", "kunming", "云南省", (102.7070, 25.0370, 102.7120, 25.0415)),
    CityRecord("拉萨", "lhasa", "西藏", (91.1310, 29.6510, 91.1360, 29.6555)),
    CityRecord("西安", "xian", "陕西省", (108.9400, 34.2570, 108.9450, 34.2615)),
    CityRecord("兰州", "lanzhou", "甘肃省", (103.8310, 36.0580, 103.8360, 36.0625)),
    CityRecord("西宁", "xining", "青海省", (101.7740, 36.6160, 101.7790, 36.6205)),
    CityRecord("银川", "yinchuan", "宁夏", (106.2720, 38.4680, 106.2770, 38.4725)),
    CityRecord("乌鲁木齐", "urumqi", "新疆", (87.6010, 43.7920, 87.6060, 43.7965)),
    # ---- GDP 前列地级市 (~25) ----
    CityRecord("深圳", "shenzhen", "广东省", (114.0570, 22.5430, 114.0620, 22.5475)),
    CityRecord("苏州", "suzhou", "江苏省", (120.6190, 31.3050, 120.6240, 31.3095)),
    CityRecord("宁波", "ningbo", "浙江省", (121.5460, 29.8660, 121.5510, 29.8705)),
    CityRecord("无锡", "wuxi", "江苏省", (120.2990, 31.5700, 120.3040, 31.5745)),
    CityRecord("佛山", "foshan", "广东省", (113.1200, 23.0210, 113.1250, 23.0255)),
    CityRecord("东莞", "dongguan", "广东省", (113.7510, 23.0200, 113.7560, 23.0245)),
    CityRecord("泉州", "quanzhou", "福建省", (118.5890, 24.9080, 118.5940, 24.9125)),
    CityRecord("大连", "dalian", "辽宁省", (121.6190, 38.9120, 121.6240, 38.9165)),
    CityRecord("青岛", "qingdao", "山东省", (120.3810, 36.0660, 120.3860, 36.0705)),
    CityRecord("烟台", "yantai", "山东省", (121.3870, 37.5350, 121.3920, 37.5395)),
    CityRecord("常州", "changzhou", "江苏省", (119.9720, 31.7810, 119.9770, 31.7855)),
    CityRecord("温州", "wenzhou", "浙江省", (120.6530, 28.0010, 120.6580, 28.0055)),
    CityRecord("珠海", "zhuhai", "广东省", (113.5670, 22.2700, 113.5720, 22.2745)),
    CityRecord("厦门", "xiamen", "福建省", (118.0870, 24.4790, 118.0920, 24.4835)),
    CityRecord("中山", "zhongshan", "广东省", (113.3800, 22.5160, 113.3850, 22.5205)),
    CityRecord("惠州", "huizhou", "广东省", (114.4120, 23.0850, 114.4170, 23.0895)),
    CityRecord("南通", "nantong", "江苏省", (120.8600, 32.0100, 120.8650, 32.0145)),
    CityRecord("绍兴", "shaoxing", "浙江省", (120.5750, 29.9960, 120.5800, 30.0005)),
    CityRecord("嘉兴", "jiaxing", "浙江省", (120.7530, 30.7470, 120.7580, 30.7515)),
    CityRecord("台州", "taizhou_zj", "浙江省", (121.4180, 28.6560, 121.4230, 28.6605)),
    CityRecord("潍坊", "weifang", "山东省", (119.1010, 36.7060, 119.1060, 36.7105)),
    CityRecord("徐州", "xuzhou", "江苏省", (117.1840, 34.2610, 117.1890, 34.2655)),
    CityRecord("洛阳", "luoyang", "河南省", (112.4530, 34.6180, 112.4580, 34.6225)),
    CityRecord("唐山", "tangshan", "河北省", (118.1750, 39.6300, 118.1800, 39.6345)),
    CityRecord("包头", "baotou", "内蒙古", (109.8370, 40.6570, 109.8420, 40.6615)),
    # ---- 地理多样性补充 (~20) ----
    CityRecord("三亚", "sanya", "海南省", (109.4980, 18.2470, 109.5030, 18.2515)),
    CityRecord("桂林", "guilin", "广西", (110.2900, 25.2740, 110.2950, 25.2785)),
    CityRecord("丽江", "lijiang", "云南省", (100.2270, 26.8720, 100.2320, 26.8765)),
    CityRecord("大理", "dali", "云南省", (100.1650, 25.6060, 100.1700, 25.6105)),
    CityRecord("威海", "weihai", "山东省", (122.1150, 37.5090, 122.1200, 37.5135)),
    CityRecord("秦皇岛", "qinhuangdao", "河北省", (119.5960, 39.9340, 119.6010, 39.9385)),
    CityRecord("黄山", "huangshan", "安徽省", (118.3370, 29.7140, 118.3420, 29.7185)),
    CityRecord("遵义", "zunyi", "贵州省", (106.9070, 27.7250, 106.9120, 27.7295)),
    CityRecord("柳州", "liuzhou", "广西", (109.4110, 24.3140, 109.4160, 24.3185)),
    CityRecord("湛江", "zhanjiang", "广东省", (110.3530, 21.2700, 110.3580, 21.2745)),
    CityRecord("廊坊", "langfang", "河北省", (116.6830, 39.5370, 116.6880, 39.5415)),
    CityRecord("芜湖", "wuhu", "安徽省", (118.3760, 31.3300, 118.3810, 31.3345)),
    CityRecord("九江", "jiujiang", "江西省", (115.9870, 29.7050, 115.9920, 29.7095)),
    CityRecord("宜昌", "yichang", "湖北省", (111.2860, 30.6910, 111.2910, 30.6955)),
    CityRecord("襄阳", "xiangyang", "湖北省", (112.1440, 32.0400, 112.1490, 32.0445)),
    CityRecord("株洲", "zhuzhou", "湖南省", (113.1340, 27.8270, 113.1390, 27.8315)),
    CityRecord("临沂", "linyi", "山东省", (118.3530, 35.0490, 118.3580, 35.0535)),
    CityRecord("赣州", "ganzhou", "江西省", (114.9330, 25.8310, 114.9380, 25.8355)),
    CityRecord("西双版纳", "xishuangbanna", "云南省", (100.7910, 22.0020, 100.7960, 22.0065)),
    CityRecord("延安", "yanan", "陕西省", (109.4890, 36.5850, 109.4940, 36.5895)),
)


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

# Pre-build indices for fast lookup
_INDEX_EN = {c.name_en.lower(): c for c in CHINA_CITY_REGISTRY}
_INDEX_ZH = {c.name_zh: c for c in CHINA_CITY_REGISTRY}


def get_city_by_name(name: str) -> Optional[CityRecord]:
    """Look up a city by Chinese or English name (case-insensitive)."""
    key = name.strip()
    hit = _INDEX_ZH.get(key)
    if hit is not None:
        return hit
    return _INDEX_EN.get(key.lower())


def get_all_bboxes() -> List[Tuple[float, float, float, float]]:
    """Return the bbox list for all registered cities."""
    return [c.bbox for c in CHINA_CITY_REGISTRY]


def get_city_choices() -> List[Tuple[str, str]]:
    """Return ``(display_label, value)`` pairs for a Gradio Dropdown.

    Format: ``("北京 Beijing", "beijing")``.
    """
    return [
        (f"{c.name_zh} {c.name_en.capitalize()}", c.name_en)
        for c in CHINA_CITY_REGISTRY
    ]
