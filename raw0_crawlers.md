# District 区县爬虫 raw=0 清单（42个）

| 序号 | 名称 | 文件名 | 目标URL | RAW=0的原因 |
|---:|---|---|---|---|
| 1 | 南京市建邺区_部门发文 | nanjing_jianye_bmwj_crawler.py | http://www.njjy.gov.cn/cszwgk/zdgk/214/224/index_18000.html | 页面 `#result` 为空，栏目可能无内容 |
| 2 | 南京市浦口区_政府办发文 | nanjing_pukou_zfbwj_crawler.py | https://njna.nanjing.gov.cn/njsjbxqglwyh/214/223/index_18009.html | 页面列表为空，栏目可能无内容 |
| 3 | 南京市雨花台区_部门发文 | nanjing_yuhuatai_bmwj_crawler.py | http://www.njyh.gov.cn/yhtqrmzf/214/224/index_18004.html | 页面列表为空，栏目可能无内容 |
| 4 | 无锡市梁溪区_部门发文 | wuxi_liangxi_bmwj_crawler.py | http://www.wxlx.gov.cn/xxgk/bmxxgkml/index.shtml | 部门信息公开目录页，非文档列表 |
| 5 | 无锡市锡山区_部门发文 | wuxi_xishan_bmwj_crawler.py | http://www.jsxishan.gov.cn/zfxxgk/bmxxgkmllm/index.shtml | 部门信息公开目录页，非文档列表 |
| 6 | 无锡市惠山区_部门发文 | wuxi_huishan_bmwj_crawler.py | http://www.huishan.gov.cn/fzlm/zqdh/index.shtml | 部门信息公开目录页，非文档列表 |
| 7 | 无锡市滨湖区_部门发文 | wuxi_binhu_bmwj_crawler.py | http://www.wxbh.gov.cn/zfxxgk/bmxxgkml/index.shtml | 部门信息公开目录页，非文档列表 |
| 8 | 无锡市新吴区_部门发文 | wuxi_xinwu_bmwj_crawler.py | http://www.wnd.gov.cn/xxgk/bmxxgkml/index.shtml | 部门信息公开目录页，非文档列表 |
| 9 | 苏州市常熟市_部门发文 | suzhou_changshu_bmwj_crawler.py | http://www.changshu.gov.cn/zgcs/fdzdgknr/csqxxgkml.shtml?para=dfbmptlj | 目录页 iframe 内容为机构职能介绍，非文件列表 |
| 10 | 苏州市张家港市_部门发文 | suzhou_zhangjiagang_bmwj_crawler.py | http://www.zjg.gov.cn/zjg/xxgkml/xxgks.shtml?temp=bumen | 目录页部门链接需 JS 渲染，无法静态提取 |
| 11 | 盐城市亭湖区_政府发文 | yc_tinghu_zfwj_crawler.py | https://www.tinghu.gov.cn/col/col32025/index.html | 云防御拦截 / JS 动态渲染 |
| 12 | 盐城市亭湖区_政府办发文 | yc_tinghu_zfbwj_crawler.py | https://www.tinghu.gov.cn/col/col32026/index.html | 云防御拦截 / JS 动态渲染 |
| 13 | 盐城市亭湖区_规范性文件 | yc_tinghu_gfxwj_crawler.py | https://www.tinghu.gov.cn/col/col32028/index.html | 云防御拦截 / JS 动态渲染 |
| 14 | 盐城市盐都区_规范性文件 | yc_yandu_gfxwj_crawler.py | https://www.yandu.gov.cn/col/col30546/index.html?number=zp002006 | JS 动态渲染，静态 HTML 无列表数据 |
| 15 | 盐城市盐都区_部门发文 | yc_yandu_bmwj_crawler.py | https://www.yandu.gov.cn/col/col32062/index.html | 部门信息公开目录页，非文档列表 |
| 16 | 盐城市大丰区_政府发文 | yc_dafeng_zfwj_crawler.py | https://www.dafeng.gov.cn/col/col12286/index.html | 云防御拦截 |
| 17 | 盐城市大丰区_政府办发文 | yc_dafeng_zfbwj_crawler.py | https://www.dafeng.gov.cn/col/col12287/index.html | 云防御拦截 |
| 18 | 盐城市大丰区_部门发文 | yc_dafeng_bmwj_crawler.py | https://www.dafeng.gov.cn/col/col24629/index.html | 云防御拦截 |
| 19 | 盐城市响水县_政府发文 | yc_xiangshui_zfwj_crawler.py | https://www.xiangshui.gov.cn/col/col11655/index.html?number=C00004C00001 | 云防御拦截 |
| 20 | 盐城市响水县_政府办发文 | yc_xiangshui_zfbwj_crawler.py | https://www.xiangshui.gov.cn/col/col44929/index.html?number=C00004C00006 | 云防御拦截 |
| 21 | 盐城市响水县_部门发文 | yc_xiangshui_bmwj_crawler.py | https://www.xiangshui.gov.cn/col/col18010/index.html | 云防御拦截 |
| 22 | 盐城市滨海县_政府发文 | yc_binhai_zfwj_crawler.py | https://www.binhai.gov.cn/col/col25867/index.html?number=H0002 | JS 动态渲染，静态 HTML 无列表数据 |
| 23 | 盐城市滨海县_政府办发文 | yc_binhai_zfbwj_crawler.py | https://www.binhai.gov.cn/col/col44895/index.html | JS 动态渲染，静态 HTML 无列表数据 |
| 24 | 盐城市滨海县_部门发文 | yc_binhai_bmwj_crawler.py | https://www.binhai.gov.cn/col/col44655/index.html | JS 动态渲染，静态 HTML 无列表数据 |
| 25 | 盐城市阜宁县_政府办发文 | yc_funing_zfbwj_crawler.py | https://www.funing.gov.cn/col/col11574/index.html | JS 动态渲染，静态 HTML 无列表数据 |
| 26 | 盐城市阜宁县_部门发文 | yc_funing_bmwj_crawler.py | https://www.funing.gov.cn/col/col24501/index.html | 部门信息公开目录页，非文档列表 |
| 27 | 盐城市射阳县_政府办发文 | yc_sheyang_zfbwj_crawler.py | https://www.sheyang.gov.cn/col/col12386/index.html?number=E00005E00003 | JS 动态渲染，静态 HTML 无列表数据 |
| 28 | 盐城市射阳县_部门发文 | yc_sheyang_bmwj_crawler.py | https://www.sheyang.gov.cn/col/col30757/index.html | 部门信息公开目录页，非文档列表 |
| 29 | 盐城市建湖县_政府办发文 | yc_jianhu_zfbwj_crawler.py | https://www.jianhu.gov.cn/col/col32124/index.html | JS 动态渲染，静态 HTML 无列表数据 |
| 30 | 盐城市建湖县_部门发文 | yc_jianhu_bmwj_crawler.py | https://www.jianhu.gov.cn/col/col31977/index.html | 部门信息公开目录页，非文档列表 |
| 31 | 盐城市东台市_政府发文 | yc_dongtai_zfwj_crawler.py | https://www.dongtai.gov.cn/col/col44785/index.html | JS 动态渲染，静态 HTML 无列表数据 |
| 32 | 盐城市东台市_政府办发文 | yc_dongtai_zfbwj_crawler.py | https://www.dongtai.gov.cn/col/col44786/index.html | JS 动态渲染，静态 HTML 无列表数据 |
| 33 | 盐城市东台市_部门发文 | yc_dongtai_bmwj_crawler.py | https://www.dongtai.gov.cn/col/col7843/index.html | 部门信息公开目录页，非文档列表 |
| 34 | 扬州市广陵区_部门发文 | yz_guangling_bmwj_crawler.py | https://gl.yangzhou.gov.cn/zfxxgk/fdzdgknr/bmxxgk/index.html | 部门信息公开目录页，非文档列表 |
| 35 | 扬州市邗江区_部门发文 | yz_hanjiang_bmwj_crawler.py | http://www.hj.gov.cn/zfxxgk/bmzfxxgk/index.html | 部门信息公开目录页，非文档列表 |
| 36 | 扬州市江都区_政府发文 | yz_jiangdu_zfwj_crawler.py | http://www.jiangdu.gov.cn/zfxxgk/fdzdgknr/zfwj/index.html | jpaas 接口参数问题（缺 pageId） |
| 37 | 扬州市江都区_部门发文 | yz_jiangdu_bmwj_crawler.py | http://www.jiangdu.gov.cn/zfxxgk/fdzdgknr/bmwj/index.html | 部门信息公开目录页，非文档列表 |
| 38 | 扬州市宝应县_部门发文 | yz_baoying_bmwj_crawler.py | https://baoying.yangzhou.gov.cn/zfxxgk/xjbmxxgk/index.html | 部门信息公开目录页，非文档列表 |
| 39 | 扬州市仪征市_部门发文 | yz_yizheng_bmwj_crawler.py | http://www.yizheng.gov.cn/zfxxgk/yqxzbmxxgkml/index.html | 部门信息公开目录页，非文档列表 |
| 40 | 扬州市高邮市_政府发文 | yz_gaoyou_zfwj_crawler.py | http://www.gaoyou.gov.cn/zwgk/fdzdgknr/zfwj/szfwj/index.html | jpaas 接口请求超时 |
| 41 | 扬州市高邮市_政府办发文 | yz_gaoyou_zfbwj_crawler.py | http://www.gaoyou.gov.cn/zwgk/fdzdgknr/zfwj/szfbwj/index.html | jpaas 接口请求超时 |
| 42 | 扬州市高邮市_部门发文 | yz_gaoyou_bmwj_crawler.py | http://www.gaoyou.gov.cn/zwgk/bmxxgk/index.html | jpaas 接口请求超时 |