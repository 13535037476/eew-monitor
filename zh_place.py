"""GlobalQuake 地名 → 中文。

GQ 的 region 字段来自 GeoNames，长这样：
    western Texas / Tarapaca, Chile / off the east coast of Honshu, Japan
    Provincia di L'Aquila, Italy / Greater Los Angeles area, California

翻译策略是「查表 + 规则」，不做机器翻译（那要联网、要 key，而且遇到地名会乱翻）：
  1. 先整句匹配少数高频固定短语（off the coast of…、near the coast of…）；
  2. 再按逗号切段，每段先查地名表，查不到就拆方位词 / 地理特征词；
  3. 全都拆不动就保留原文——宁可留英文，也不要瞎猜出一个错的中文名。

政治表述在这份文件里是硬约束：
  · Taiwan 一律「中国台湾」，Hong Kong「中国香港」，Macao/Macau「中国澳门」；
  · 台湾的县市写成「中国台湾 花莲」，不出现任何把台湾当国家的写法；
  · 词表里不收录任何「台湾国 / 中华民国」之类的键。

第一优先级是 ISC Flinn-Engdahl 分区地名库（data/fe_regions.json，754 条在用分区，
由 tools/build_fe_regions.py 从 E:\\FEregions分区 编译）。命中就直用库里的中文，
不再自己拼；只有库里没有的地名（GeoNames 的市/县级写法）才走下面那套规则。
"""

import json
import os
import re
from functools import lru_cache

# ---------------- 国家与地区 ----------------
COUNTRY = {
    "japan": "日本", "china": "中华人民共和国", "taiwan": "中国台湾", "hong kong": "中国香港",
    "macau": "中国澳门", "macao": "中国澳门",
    "south korea": "韩国", "korea": "朝鲜半岛", "north korea": "朝鲜",
    "philippines": "菲律宾", "indonesia": "印度尼西亚", "malaysia": "马来西亚",
    "singapore": "新加坡", "thailand": "泰国", "vietnam": "越南", "myanmar": "缅甸",
    "laos": "老挝", "cambodia": "柬埔寨", "india": "印度", "pakistan": "巴基斯坦",
    "nepal": "尼泊尔", "bhutan": "不丹", "bangladesh": "孟加拉国", "sri lanka": "斯里兰卡",
    "afghanistan": "阿富汗", "iran": "伊朗", "iraq": "伊拉克", "turkey": "土耳其",
    "syria": "叙利亚", "lebanon": "黎巴嫩", "israel": "以色列", "jordan": "约旦",
    "saudi arabia": "沙特阿拉伯", "yemen": "也门", "oman": "阿曼",
    "united arab emirates": "阿联酋", "qatar": "卡塔尔", "kuwait": "科威特",
    "georgia": "格鲁吉亚", "armenia": "亚美尼亚", "azerbaijan": "阿塞拜疆",
    "kazakhstan": "哈萨克斯坦", "uzbekistan": "乌兹别克斯坦", "kyrgyzstan": "吉尔吉斯斯坦",
    "tajikistan": "塔吉克斯坦", "turkmenistan": "土库曼斯坦", "mongolia": "蒙古",
    "russia": "俄罗斯", "ukraine": "乌克兰", "belarus": "白俄罗斯",
    "poland": "波兰", "germany": "德国", "france": "法国", "italy": "意大利",
    "spain": "西班牙", "portugal": "葡萄牙", "greece": "希腊", "albania": "阿尔巴尼亚",
    "romania": "罗马尼亚", "bulgaria": "保加利亚", "serbia": "塞尔维亚",
    "croatia": "克罗地亚", "slovenia": "斯洛文尼亚", "bosnia and herzegovina": "波黑",
    "hungary": "匈牙利", "austria": "奥地利", "switzerland": "瑞士",
    "czech republic": "捷克", "czechia": "捷克", "slovakia": "斯洛伐克",
    "netherlands": "荷兰", "belgium": "比利时", "denmark": "丹麦", "norway": "挪威",
    "sweden": "瑞典", "finland": "芬兰", "iceland": "冰岛", "ireland": "爱尔兰",
    "united kingdom": "英国", "england": "英格兰", "scotland": "苏格兰", "wales": "威尔士",
    "united states": "美国", "usa": "美国", "canada": "加拿大", "mexico": "墨西哥",
    "guatemala": "危地马拉", "honduras": "洪都拉斯", "el salvador": "萨尔瓦多",
    "nicaragua": "尼加拉瓜", "costa rica": "哥斯达黎加", "panama": "巴拿马",
    "cuba": "古巴", "haiti": "海地", "dominican republic": "多米尼加",
    "jamaica": "牙买加", "puerto rico": "波多黎各", "trinidad and tobago": "特立尼达和多巴哥",
    "colombia": "哥伦比亚", "venezuela": "委内瑞拉", "ecuador": "厄瓜多尔",
    "peru": "秘鲁", "bolivia": "玻利维亚", "brazil": "巴西", "chile": "智利",
    "argentina": "阿根廷", "uruguay": "乌拉圭", "paraguay": "巴拉圭",
    "australia": "澳大利亚", "new zealand": "新西兰", "papua new guinea": "巴布亚新几内亚",
    "fiji": "斐济", "vanuatu": "瓦努阿图", "solomon islands": "所罗门群岛",
    "samoa": "萨摩亚", "tonga": "汤加", "new caledonia": "新喀里多尼亚",
    "south africa": "南非", "egypt": "埃及", "morocco": "摩洛哥", "algeria": "阿尔及利亚",
    "tunisia": "突尼斯", "libya": "利比亚", "sudan": "苏丹", "ethiopia": "埃塞俄比亚",
    "kenya": "肯尼亚", "tanzania": "坦桑尼亚", "uganda": "乌干达", "madagascar": "马达加斯加",
    "mozambique": "莫桑比克", "zimbabwe": "津巴布韦", "zambia": "赞比亚",
    "democratic republic of the congo": "刚果（金）", "congo": "刚果（布）",
    "cameroon": "喀麦隆", "ghana": "加纳", "nigeria": "尼日利亚", "mali": "马里",
}

# ---------------- 一级行政区 ----------------
REGION = {
    # 日本都道府县
    "hokkaido": "北海道", "aomori": "青森县", "iwate": "岩手县", "miyagi": "宫城县",
    "akita": "秋田县", "yamagata": "山形县", "fukushima": "福岛县", "ibaraki": "茨城县",
    "tochigi": "栃木县", "gunma": "群马县", "saitama": "埼玉县", "chiba": "千叶县",
    "tokyo": "东京都", "kanagawa": "神奈川县", "niigata": "新潟县", "toyama": "富山县",
    "ishikawa": "石川县", "fukui": "福井县", "yamanashi": "山梨县", "nagano": "长野县",
    "gifu": "岐阜县", "shizuoka": "静冈县", "aichi": "爱知县", "mie": "三重县",
    "shiga": "滋贺县", "kyoto": "京都府", "osaka": "大阪府", "hyogo": "兵库县",
    "nara": "奈良县", "wakayama": "和歌山县", "tottori": "鸟取县", "shimane": "岛根县",
    "okayama": "冈山县", "hiroshima": "广岛县", "yamaguchi": "山口县", "tokushima": "德岛县",
    "kagawa": "香川县", "ehime": "爱媛县", "kochi": "高知县", "fukuoka": "福冈县",
    "saga": "佐贺县", "nagasaki": "长崎县", "kumamoto": "熊本县", "oita": "大分县",
    "miyazaki": "宫崎县", "kagoshima": "鹿儿岛县", "okinawa": "冲绳县",
    # 日本大地名
    "honshu": "本州", "kyushu": "九州", "shikoku": "四国", "ryukyu islands": "琉球群岛",
    "ryukyu": "琉球群岛", "izu islands": "伊豆群岛", "bonin islands": "小笠原群岛",
    # 中国省级
    "beijing": "北京", "tianjin": "天津", "hebei": "河北", "shanxi": "山西",
    "inner mongolia": "内蒙古", "liaoning": "辽宁", "jilin": "吉林", "heilongjiang": "黑龙江",
    "shanghai": "上海", "jiangsu": "江苏", "zhejiang": "浙江", "anhui": "安徽",
    "fujian": "福建", "jiangxi": "江西", "shandong": "山东", "henan": "河南",
    "hubei": "湖北", "hunan": "湖南", "guangdong": "广东", "guangxi": "广西",
    "hainan": "海南", "chongqing": "重庆", "sichuan": "四川", "guizhou": "贵州",
    "yunnan": "云南", "tibet": "西藏", "xizang": "西藏", "shaanxi": "陕西",
    "gansu": "甘肃", "qinghai": "青海", "ningxia": "宁夏", "xinjiang": "新疆",
    # 台湾
    "taipei": "台北", "new taipei": "新北", "taoyuan": "桃园", "taichung": "台中",
    "tainan": "台南", "kaohsiung": "高雄", "keelung": "基隆", "hsinchu": "新竹",
    "miaoli": "苗栗", "changhua": "彰化", "nantou": "南投", "yunlin": "云林",
    "chiayi": "嘉义", "pingtung": "屏东", "yilan": "宜兰", "ilan": "宜兰",
    "hualien": "花莲", "taitung": "台东", "penghu": "澎湖", "kinmen": "金门",
    "lienchiang": "连江", "matsu": "马祖",
    # 美国州（地震常用的那些）
    "california": "加利福尼亚", "alaska": "阿拉斯加", "hawaii": "夏威夷",
    "washington": "华盛顿州", "oregon": "俄勒冈", "nevada": "内华达",
    "utah": "犹他", "idaho": "爱达荷", "montana": "蒙大拿", "wyoming": "怀俄明",
    "colorado": "科罗拉多", "new mexico": "新墨西哥", "arizona": "亚利桑那",
    "texas": "得克萨斯", "oklahoma": "俄克拉何马", "kansas": "堪萨斯",
    "nebraska": "内布拉斯加", "south dakota": "南达科他", "north dakota": "北达科他",
    "minnesota": "明尼苏达", "iowa": "艾奥瓦", "missouri": "密苏里",
    "arkansas": "阿肯色", "louisiana": "路易斯安那", "mississippi": "密西西比",
    "alabama": "亚拉巴马", "georgia (us)": "佐治亚", "florida": "佛罗里达",
    "south carolina": "南卡罗来纳", "north carolina": "北卡罗来纳",
    "tennessee": "田纳西", "kentucky": "肯塔基", "virginia": "弗吉尼亚",
    "west virginia": "西弗吉尼亚", "maryland": "马里兰", "delaware": "特拉华",
    "pennsylvania": "宾夕法尼亚", "new jersey": "新泽西", "new york": "纽约州",
    "connecticut": "康涅狄格", "rhode island": "罗得岛", "massachusetts": "马萨诸塞",
    "vermont": "佛蒙特", "new hampshire": "新罕布什尔", "maine": "缅因",
    "michigan": "密歇根", "wisconsin": "威斯康星", "illinois": "伊利诺伊",
    "indiana": "印第安纳", "ohio": "俄亥俄",
}

# ---------------- 常见城市 / 地名 ----------------
PLACE = {
    "tokyo": "东京", "osaka": "大阪", "kyoto": "京都", "yokohama": "横滨",
    "nagoya": "名古屋", "sapporo": "札幌", "sendai": "仙台", "fukuoka": "福冈",
    "kobe": "神户", "hiroshima": "广岛", "nagasaki": "长崎", "kumamoto": "熊本",
    "kagoshima": "鹿儿岛", "niigata": "新潟", "kanazawa": "金泽", "shizuoka": "静冈",
    "chiba": "千叶", "kawasaki": "川崎", "saitama": "埼玉", "hachinohe": "八户",
    "aomori": "青森", "akita": "秋田", "yamagata": "山形", "morioka": "盛冈",
    "fukushima": "福岛", "mito": "水户", "utsunomiya": "宇都宫", "maebashi": "前桥",
    "kofu": "甲府", "nagano": "长野", "toyama": "富山", "fukui": "福井",
    "gifu": "岐阜", "tsu": "津", "otsu": "大津", "nara": "奈良",
    "wakayama": "和歌山", "tottori": "鸟取", "matsue": "松江", "okayama": "冈山",
    "yamaguchi": "山口", "tokushima": "德岛", "takamatsu": "高松", "matsuyama": "松山",
    "kochi": "高知", "saga": "佐贺", "oita": "大分", "miyazaki": "宫崎",
    "naha": "那霸", "hakodate": "函馆", "asahikawa": "旭川", "kushiro": "钏路",
    "los angeles": "洛杉矶", "san francisco": "旧金山", "san diego": "圣迭戈",
    "seattle": "西雅图", "portland": "波特兰", "las vegas": "拉斯维加斯",
    "salt lake city": "盐湖城", "denver": "丹佛", "houston": "休斯敦",
    "dallas": "达拉斯", "austin": "奥斯汀", "chicago": "芝加哥",
    "new york": "纽约", "boston": "波士顿", "miami": "迈阿密",
    "anchorage": "安克雷奇", "fairbanks": "费尔班克斯", "honolulu": "檀香山",
    "hilo": "希洛", "vancouver": "温哥华", "mexico city": "墨西哥城",
    "santiago": "圣地亚哥", "lima": "利马", "quito": "基多", "bogota": "波哥大",
    "wellington": "惠灵顿", "auckland": "奥克兰", "christchurch": "基督城",
    "manila": "马尼拉", "jakarta": "雅加达", "bangkok": "曼谷", "hanoi": "河内",
    "tehran": "德黑兰", "istanbul": "伊斯坦布尔", "athens": "雅典", "rome": "罗马",
    "lisbon": "里斯本", "madrid": "马德里", "paris": "巴黎", "london": "伦敦",
    "reykjavik": "雷克雅未克", "cairo": "开罗", "nairobi": "内罗毕",
    "cape town": "开普敦", "johannesburg": "约翰内斯堡",
    # 海域名（GQ 的 region 常直接就是一个海）
    # 全球地震带上的高频地名（GQ 事件集中在环太平洋 + 地中海—喜马拉雅带）
    "mauritius - reunion region": "毛里求斯—留尼汪地区",
    "mauritius - reunion": "毛里求斯—留尼汪",
    "nelson": "纳尔逊", "hawke's bay": "霍克斯湾", "guadeloupe": "瓜德罗普",
    "leeward islands": "背风群岛", "windward islands": "向风群岛",
    "hawaiian islands": "夏威夷群岛", "mauritius": "毛里求斯", "reunion": "留尼汪",
    "georgia (sak'art'velo)": "格鲁吉亚", "sak'art'velo": "格鲁吉亚",
    "western australia": "西澳大利亚", "new south wales": "新南威尔士",
    "andreanof islands": "安德烈亚诺夫群岛", "near islands": "尼尔群岛",
    "prince william sound": "威廉王子湾", "kodiak": "科迪亚克", "anchorage": "安克雷奇",
    "aleutians": "阿留申群岛", "bering strait": "白令海峡", "denali": "德纳里",
    "ridgecrest": "里奇克莱斯特", "mammoth lakes": "马默斯湖", "san andreas": "圣安德烈亚斯",
    "sicily": "西西里", "etna": "埃特纳", "naples": "那不勒斯", "amarice": "阿马特里切",
    "amatrice": "阿马特里切", "norcia": "诺尔恰", "umbria": "翁布里亚", "abruzzo": "阿布鲁佐",
    "crete": "克里特", "santorini": "圣托里尼", "athens": "雅典", "peloponnese": "伯罗奔尼撒",
    "anatolia": "安纳托利亚", "izmir": "伊兹密尔", "kahramanmaras": "卡赫拉曼马拉什",
    "elazig": "埃拉泽", "van": "凡城", "kermanshah": "克尔曼沙赫", "tabriz": "大不里士",
    "visayas": "米沙鄢", "leyte": "莱特", "samar": "萨马", "negros": "内格罗斯",
    "bali": "巴厘", "lombok": "龙目", "halmahera": "哈马黑拉", "seram": "塞兰",
    "flores": "弗洛勒斯", "sumba": "松巴", "talaud": "塔劳", "ternate": "特尔纳特",
    "new britain": "新不列颠", "new ireland": "新爱尔兰", "bougainville": "布干维尔",
    "kaikoura": "凯库拉", "canterbury": "坎特伯雷", "fiordland": "峡湾",
    "arequipa": "阿雷基帕", "cusco": "库斯科", "puno": "普诺", "tacna": "塔克纳",
    "oaxaca": "瓦哈卡", "chiapas": "恰帕斯", "guerrero": "格雷罗", "michoacan": "米却肯",
    "jalisco": "哈利斯科", "colima": "科利马", "veracruz": "韦拉克鲁斯",
    "reykjanes": "雷克雅内斯", "mid-atlantic ridge": "大西洋中脊",
    "alberta": "艾伯塔", "british columbia": "不列颠哥伦比亚", "ontario": "安大略",
    "quebec": "魁北克", "manitoba": "马尼托巴", "saskatchewan": "萨斯喀彻温",
    "jujuy": "胡胡伊", "salta": "萨尔塔", "mendoza": "门多萨", "tucuman": "图库曼",
    "bio-bio": "比奥比奥", "biobio": "比奥比奥", "coquimbo": "科金博",
    "hindu kush": "兴都库什", "timor": "帝汶", "timor-leste": "东帝汶",
    "fox islands": "福克斯群岛", "kenai peninsula": "基奈半岛",
    "north island": "北岛", "south island": "南岛", "aleutian islands": "阿留申群岛",
    "tarapaca": "塔拉帕卡", "antofagasta": "安托法加斯塔", "atacama": "阿塔卡马",
    "coquimbo": "科金博", "valparaiso": "瓦尔帕莱索", "maule": "马乌莱",
    "l'aquila": "拉奎拉", "aquila": "拉奎拉", "hastings": "黑斯廷斯",
    "banda sea": "班达海", "molucca sea": "马鲁古海", "flores sea": "弗洛勒斯海",
    "java sea": "爪哇海", "celebes sea": "苏拉威西海", "sulu sea": "苏禄海",
    "philippine sea": "菲律宾海", "south china sea": "南海", "east china sea": "东海",
    "yellow sea": "黄海", "japan sea": "日本海", "sea of japan": "日本海",
    "okhotsk sea": "鄂霍次克海", "sea of okhotsk": "鄂霍次克海",
    "bering sea": "白令海", "tasman sea": "塔斯曼海", "coral sea": "珊瑚海",
    "timor sea": "帝汶海", "arafura sea": "阿拉弗拉海", "andaman sea": "安达曼海",
    "mediterranean sea": "地中海", "black sea": "黑海", "aegean sea": "爱琴海",
    "caribbean sea": "加勒比海", "gulf of mexico": "墨西哥湾",
    "gulf of california": "加利福尼亚湾", "gulf of alaska": "阿拉斯加湾",
    "pacific ocean": "太平洋", "atlantic ocean": "大西洋", "indian ocean": "印度洋",
    "southern ocean": "南大洋", "arctic ocean": "北冰洋",
    # 群岛 / 岛
    "kermadec islands": "克马德克群岛", "fiji islands": "斐济群岛",
    "santa cruz islands": "圣克鲁斯群岛", "loyalty islands": "洛亚蒂群岛",
    "society islands": "社会群岛", "mariana islands": "马里亚纳群岛",
    "caroline islands": "加罗林群岛", "marshall islands": "马绍尔群岛",
    "aleutian islands": "阿留申群岛", "kuril islands": "千岛群岛",
    "sakhalin": "萨哈林岛", "mindanao": "棉兰老岛", "luzon": "吕宋岛",
    "sumatra": "苏门答腊", "java": "爪哇", "borneo": "婆罗洲",
    "sulawesi": "苏拉威西", "new guinea": "新几内亚", "taiwan island": "中国台湾岛",
    "honshu": "本州", "hokkaido": "北海道", "kyushu": "九州", "shikoku": "四国",
}

# ---------------- 方位与地理特征 ----------------
# 二级行政区 → 国家。GQ 的 region 常常只给州/省不给国家（western Texas、Utah、
# Central Alaska），列表里就成了「无主之地」。这里补一层归属，只在整串地名
# 里没有任何国家名时才加前缀。
REGION_HOME = {}
# 注意：这里必须一项一项列，不能用 "a b c".split()——那会把 "new south wales"
# 拆成 new/south/wales 三个键，于是任何带 "new" 的地名（New Zealand!）都会被
# 误判成澳大利亚。之前 "Ruapehu District, New Zealand" 变成「澳大利亚 新西兰…」
# 就是这个坑。
_US_STATES = (
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "hawaii", "idaho", "illinois",
    "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine", "maryland",
    "massachusetts", "michigan", "minnesota", "mississippi", "missouri",
    "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming", "puerto rico",
)
_CA_PROV = (
    "alberta", "british columbia", "manitoba", "new brunswick",
    "newfoundland and labrador", "nova scotia", "ontario",
    "prince edward island", "quebec", "saskatchewan",
)
_AU_STATES = (
    "new south wales", "queensland", "south australia", "tasmania",
    "victoria", "western australia",
)
for _names, _home in ((_US_STATES, "美国"), (_CA_PROV, "加拿大"), (_AU_STATES, "澳大利亚")):
    for _k in _names:
        REGION_HOME[_k] = _home
# 长的先匹配：「new south wales」不能被「south australia」那种短键抢走
_REGION_HOME_KEYS = sorted(REGION_HOME, key=len, reverse=True)


def _region_home(low):
    """整串地名里没有任何国家名时，按州/省补一个归属。"""
    for k in _REGION_HOME_KEYS:
        if re.search(r"\b%s\b" % re.escape(k), low):
            return REGION_HOME[k]
    return None


DIRW = {
    "north": "北", "south": "南", "east": "东", "west": "西",
    "central": "中部", "middle": "中部",
    "northern": "北部", "southern": "南部", "eastern": "东部", "western": "西部",
    "central ": "中部",
    "northeast": "东北", "northwest": "西北", "southeast": "东南", "southwest": "西南",
    "northeastern": "东北部", "northwestern": "西北部",
    "southeastern": "东南部", "southwestern": "西南部",
    "upper": "上游", "lower": "下游", "near": "近", "off": "近海",
}

FEATW = {
    "region": "地区", "area": "地区", "district": "区", "province": "省",
    "prefecture": "县", "county": "县", "state": "州", "island": "岛",
    "islands": "群岛", "peninsula": "半岛", "coast": "海岸", "coastal": "沿海",
    "sea": "海", "gulf": "湾", "bay": "湾", "strait": "海峡", "channel": "水道",
    "trench": "海沟", "ridge": "海岭", "basin": "盆地", "valley": "谷地",
    "mountain": "山", "mountains": "山脉", "volcano": "火山", "lake": "湖",
    "river": "河", "delta": "三角洲", "plateau": "高原", "desert": "沙漠",
    "archipelago": "群岛", "atoll": "环礁", "reef": "礁", "bank": "浅滩",
    "city": "市", "town": "镇", "village": "村", "greater": "大",
}


def _clean(s):
    return " ".join(str(s or "").replace("\u00a0", " ").split())


# ---------------- ISC Flinn-Engdahl 分区地名库 ----------------
# data/fe_regions.json 由 tools/build_fe_regions.py 从 E:\FEregions分区 编译：
#   [[编号, 英文原名, 中文, 大区号], ...]，共 757 条（3 条停用）。
# 命中就直接用库里的中文——这是 ISC 的官方分区译名，比按词拼出来的靠谱。
_FE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "data", "fe_regions.json")
_FE_ROWS = []          # [(归一化英文, token 集合, 中文, 编号)]
_FE_BY_KEY = {}        # 归一化英文 → (中文, 编号)
_FE_LOADED = False

# 库里没有、但 GeoNames 常用的写法（同义词归一）
_FE_SYN = {
    "SUMATRA": "SUMATERA",
    "SULAWESI": "CELEBES",
    "BURMA": "MYANMAR",
    "TIBET": "XIZANG",        # FE 用拼音 XIZANG；统一到 #306
    "CALIF": "CALIFORNIA",    # CALIF.-BAJA CALIF. BORDER REGION
}

# 方位词必须两边一致：EASTERN HONSHU 绝不能配到 WESTERN HONSHU
_FE_DIR = {
    "NORTH", "SOUTH", "EAST", "WEST", "CENTRAL", "NORTHEAST", "NORTHWEST",
    "SOUTHEAST", "SOUTHWEST", "NORTHERN", "SOUTHERN", "EASTERN", "WESTERN",
    "NE", "NW", "SE", "SW",
}

# FE 的「地区名，国家名」写法 → 改成我们界面上惯用的「国家 地区名」。
# 只处理真正的国家/地区，州名和岛链（阿拉斯加、阿留申群岛…）保持原样。
_FE_COUNTRY_SUFFIX = set(COUNTRY.values()) | {
    "中国", "美国", "密克罗尼西亚", "基里巴斯", "马绍尔群岛",
    "新喀里多尼亚", "波多黎各", "南极洲", "法属波利尼西亚",
}


def _fe_norm(s):
    t = str(s or "").upper().replace("–", "-").replace("—", "-").replace("’", "'")
    # 缩写展开（先做多字母，再做单字母方位，免得 N.Z. 被拆成 NORTH ZEALAND）
    t = re.sub(r"\bP\.?\s*N\.?\s*G\.?\b", " PAPUA NEW GUINEA ", t)
    t = re.sub(r"\bN\.?\s*Z\.?\b", " NEW ZEALAND ", t)
    t = re.sub(r"\bISL\.?(?!\w)", " ISLANDS ", t)
    t = re.sub(r"\bIS\.(?!\w)", " ISLANDS ", t)
    t = re.sub(r"\bREG\.?(?!\w)", " REGION ", t)
    t = re.sub(r"\bBORD\.?(?!\w)", " BORDER ", t)
    t = re.sub(r"\bMT\.?(?!\w)", " MOUNT ", t)
    for ab, full in (("N", "NORTH"), ("S", "SOUTH"), ("E", "EAST"), ("W", "WEST")):
        t = re.sub(r"\b%s\.(?!\w)" % ab, " %s " % full, t)
    t = re.sub(r"\b(THE|OF)\b", " ", t)
    t = re.sub(r"[^A-Z0-9]+", " ", t)
    toks = [_FE_SYN.get(w, w) for w in t.split()]
    return " ".join(toks)


def _fe_load():
    global _FE_ROWS, _FE_BY_KEY, _FE_LOADED
    if _FE_LOADED:
        return
    _FE_LOADED = True
    try:
        with open(_FE_PATH, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return
    for rid, en, zh, _aid in d.get("r") or []:
        if "(NOT IN USE)" in en or "停用" in zh:
            continue
        key = _fe_norm(en)
        if not key:
            continue
        _FE_ROWS.append((key, set(key.split()), zh, rid))
        _FE_BY_KEY.setdefault(key, (zh, rid))


def _fe_style(zh):
    """「本州东岸近海，日本」→「日本 本州东岸近海」，和界面其他地名保持一致。"""
    m = re.match(r"^(.*)，中国[（(](.+)[)）]$", zh)
    if m:                      # 南疆，中国(新疆南部) → 中华人民共和国 南疆（新疆南部）
        return "中华人民共和国 %s（%s）" % (m.group(1), m.group(2))
    if "，" in zh:
        parts = zh.split("，")
        last = parts[-1]
        if last == "中国":
            last = "中华人民共和国"
        if last in _FE_COUNTRY_SUFFIX:
            return (last + " " + "".join(parts[:-1])).strip()
    return zh


@lru_cache(maxsize=4096)
def fe_lookup(s):
    """查 FE 分区库。返回中文或 None（None 表示库里确实没有，交给规则兜底）。

    先精确匹配（缩写/标点/大小写都归一过），再按 token Dice 相似度兜底：
    要求方位词完全一致、相似度 ≥0.75、且第一名明显领先第二名——
    宁可回去走规则，也不要在地震卡片上猜错一个地区。
    """
    _fe_load()
    if not _FE_ROWS:
        return None
    key = _fe_norm(s)
    if not key:
        return None
    hit = _FE_BY_KEY.get(key)
    if hit:
        return _fe_style(hit[0])
    q = set(key.split())
    if not q:
        return None
    qdir = q & _FE_DIR
    best = None
    second = 0.0
    for _k, toks, zh, rid in _FE_ROWS:
        if (toks & _FE_DIR) != qdir:
            continue
        inter = len(q & toks)
        if not inter:
            continue
        dice = 2.0 * inter / (len(q) + len(toks))
        if best is None or dice > best[0]:
            second = best[0] if best else 0.0
            best = (dice, zh, rid)
        elif dice > second:
            second = dice
    if not best or best[0] < 0.75:
        return None
    if best[0] - second < 0.05:
        return None
    return _fe_style(best[1])


def fe_info(s):
    """给调试/界面用：命中的分区编号与中文（不命中返回 None）。"""
    _fe_load()
    key = _fe_norm(s)
    if not key:
        return None
    hit = _FE_BY_KEY.get(key)
    if hit:
        return {"id": hit[1], "zh": hit[0], "exact": True}
    zh = fe_lookup(s)
    if zh:
        return {"id": None, "zh": zh, "exact": False}
    return None


def _cap(s):
    """保留专名但把每个词首字母大写，便于查表"""
    return " ".join(w[:1].upper() + w[1:].lower() if w else w for w in s.split())


def _lookup(seg, table):
    k = seg.strip().lower().rstrip(".")
    if k in table:
        return table[k]
    # 去掉行政后缀再试一次：Provincia di L'Aquila → l'aquila
    for pre in ("provincia di ", "province of ", "state of ", "region of ",
                "district of ", "department of ", "oblast of ", "prefecture of "):
        if k.startswith(pre):
            k2 = k[len(pre):]
            if k2 in table:
                return table[k2]
    return None


def _translate_seg(seg):
    """一段（逗号之间）的翻译。返回中文或 None"""
    raw = _clean(seg)
    if not raw:
        return None
    low = raw.lower()
    if low.startswith("the "):
        raw, low = raw[4:], low[4:]

    # ① 整段直接命中
    for table in (COUNTRY, REGION, PLACE):
        hit = _lookup(low, table)
        if hit:
            return hit

    # ② 「方位词 + 地名」：western Texas / northern Chile
    parts = low.split(" ", 1)
    if len(parts) == 2 and parts[0] in DIRW:
        rest = _translate_seg(parts[1])
        if rest:
            suf = DIRW[parts[0]]
            if parts[0] in ("north", "south", "east", "west"):
                suf += "部"                   # West Greece → 希腊西部
            return rest + suf                 # 得克萨斯西部 / 伊朗南部

    # ③ 「地名 + 行政后缀」：Tarapaca Region / Hastings District
    parts = low.rsplit(" ", 1)
    if len(parts) == 2 and parts[1] in FEATW:
        base = _translate_seg(parts[0])
        if base:
            suf = FEATW[parts[1]]
            return base if base.endswith(suf) else base + suf

    # ④ offshore X → X 近海
    if low.startswith("offshore "):
        base = _translate_seg(low[9:])
        if base:
            return base + "近海"

    # ⑤ X of Y（the North Island of New Zealand）→ 先翻 X，Y 留给外层做国家前缀
    if " of " in low:
        head, tail = low.split(" of ", 1)
        h = _translate_seg(head)
        t = _translate_seg(tail)
        if h and t:
            return h if not t else (h + t if len(t) > 3 else h)
        if h:
            return h

    # ⑥ Greater X area
    if low.startswith("greater ") and low.endswith(" area"):
        base = _translate_seg(low[8:-5])
        if base:
            return base + "一带"

    return None


def _polish(res):
    """政治表述兜底：无论走哪条分支，都不允许出现把台湾/香港/澳门当国家的写法。"""
    res = res.replace("台湾,", "中国台湾,").replace("台湾 ", "中国台湾 ")
    res = res.replace("香港,", "中国香港,").replace("澳门,", "中国澳门,")
    res = res.replace("中国中国台湾", "中国台湾").replace("中国中国香港", "中国香港")
    return res


def zh_place(s):
    """把 GQ 的英文 region 翻成中文；翻不动的部分保留原文。"""
    src = _clean(s)
    if not src:
        return src

    # 缩写先还原，后面查库和规则都受益（Bougainville region, P.N.G.）
    src = re.sub(r"\bP\.?\s*N\.?\s*G\.?\b", "Papua New Guinea", src, flags=re.I)
    src = re.sub(r"\bN\.?\s*Z\.?\b", "New Zealand", src, flags=re.I)
    src = re.sub(r"\s*\.\s*$", "", src)      # 去掉句尾的点（P.N.G. 展开后会剩一个）

    # 第一优先级：ISC Flinn-Engdahl 分区地名库（754 条官方译名）
    fe = fe_lookup(src)
    if fe:
        res = fe
        # Utah → 美国 犹他州（库里只有州名）；但「加利福尼亚—下加利福尼亚交界地区」
        # 这种跨境条目已经写清两边了，再补「美国」反而是错的
        home = _region_home(src.lower())
        if home and home not in res and "交界" not in res:
            res = home + " " + res
        return _polish(res)

    low = src.lower()

    # 高频固定句式，先整体处理，免得被逗号切碎
    for pre, zh in (
        ("off the east coast of ", "东岸近海"),
        ("off the west coast of ", "西岸近海"),
        ("off the north coast of ", "北岸近海"),
        ("off the south coast of ", "南岸近海"),
        ("off the coast of ", "近海"),
        ("off east coast of ", "东岸近海"),
        ("off west coast of ", "西岸近海"),
        ("near the east coast of ", "东岸附近"),
        ("near the west coast of ", "西岸附近"),
        ("near the coast of ", "沿岸"),
        ("near the south coast of ", "南岸附近"),
        ("near the north coast of ", "北岸附近"),
        ("east of ", "以东"),
        ("west of ", "以西"),
        ("north of ", "以北"),
        ("south of ", "以南"),
    ):
        if low.startswith(pre):
            tail = src[len(pre):]
            # 「the North Island of New Zealand」：把国家拎到最前，别在拼接时丢掉
            tlow = tail.lower()
            if " of " in tlow:
                head, _, rest = tlow.partition(" of ")
                if rest.strip() in COUNTRY:
                    mid = zh_place(tail[:len(head)])
                    if mid:
                        return COUNTRY[rest.strip()] + " " + mid + zh
            mid = zh_place(tail) if tail else ""
            if mid:
                return mid + zh          # 本州 → 本州东岸近海 / 斐济群岛 → 斐济群岛以南

    segs = [x.strip() for x in src.split(",") if x.strip()]
    out = []
    for seg in segs:
        zh = _translate_seg(seg)
        out.append(zh if zh else _clean(seg))
    # 中文地名习惯「国家 + 地区」：「日本本州东岸近海」而不是「本州，日本东岸近海」
    if len(out) >= 2 and segs[-1].strip().rstrip(".").lower() in COUNTRY:
        res = out[-1] + " " + "".join(out[:-1])
    else:
        res = "，".join(out)

    # 只有州/省没有国家时补上归属：western Texas → 美国 得克萨斯西部
    home = _region_home(low)
    if home and home not in res:      # 「西澳大利亚」里已经有「澳大利亚」，别再叠一层
        res = home + " " + res

    return _polish(res)
