# 全库重解析差异盘点（2026-09-26）

- 盘点源页：3527；规则层重解析（无 LLM/VLM），对比口径见脚本头注。

| 分类 | 页数 | 说明 |
|---|---|---|
| field_diff | 1540 | 逐场字段差异（可行动） |
| unchanged | 1330 | 无差异 |
| field_diff_llm | 548 | 字段差异仅因老记录 LLM 富化（预期内，不作为落库依据） |
| parse_empty | 44 | 新解析判空（全量重爬会丢失，需核对回顾判定） |
| split_reverted | 32 | 多场回退为单条（需人工判断是否回归） |
| split_new | 26 | 旧1条→新拆多场（候选升级新收益） |
| split_count_change | 6 | 已拆页场次数变化 |
| unreachable | 1 | 源页不可达（404/超时） |

## split_new（26 页）

- http://life.scnu.edu.cn/a/20260623/6777.html（旧1→新2） 新场样例: 1:早期发育进程中表观基因组重塑@2026-06-23 15:10:00; 2:早期发育进程中表观基因组重塑@2026-06-23 15:10:00
- http://lswh.scnu.edu.cn/a/20250328/54.html（旧1→新2） 新场样例: 1:中国社会科学文摘@2025-03-28 15:00:00; 2:历史学@2025-03-28 15:00:00
- http://wxy.scnu.edu.cn/keyanxinxi/xueshuhuodong/2024/0913/3553.html（旧1→新2） 新场样例: 1:5—12@2024-09-28 10:55:00; 2:0—1 5@2024-09-28 15:10:00
- http://ctld.scnu.edu.cn/a/20230923/4203.html（旧1→新2） 新场样例: 1:数字化转型赋能课程教学创新@2023-09-27 15:00:00; 2:基于知识图谱和AI构建智慧教学新生态@2023-09-27 16:10:00
- http://bd.scnu.edu.cn/a/20230618/56.html（旧1→新4） 新场样例: 1:GNSS应用与服务的性能指标当论付迎春@2023-06-19 15:05:00; 2:基于SuperMap GIS的智慧城市关键技术研@2023-06-19 15:25:00; 3:亚像元城市不透水面的多时序优化监测 16:05-@2023-06-19 15:45:00
- http://ctld.scnu.edu.cn/a/20220302/3973.html（旧1→新2） 新场样例: 1:精耕细作,润物无声——《政治经济学》课程思政建设@2022-03-10 14:45:00; 2:精耕细作,润物无声——《政治经济学》课程思政建设@2022-03-10 14:45:00
- http://psy.scnu.edu.cn/a/20201228/2064.html（旧1→新2） 新场样例: 1:大型人脑偏侧化研究@2020-12-28 15:00:00; 2:MEG deep source imaging @2020-12-28 15:00:00
- http://ggy.scnu.edu.cn/a/20201120/5326.html（旧1→新3） 新场样例: 1:播放学院宣传片@2020-11-21 08:45:00; 2:议题一:面向2035年的国家治理:机遇与挑战@2020-11-21 09:00:00; 3:主持人:胡中锋院长@2020-11-21 15:00:00
- http://psy.scnu.edu.cn/a/20181022/1595.html（旧1→新3） 新场样例: 1:Undoing the Unwanted@2018-10-25 09:30:00; 2:无意识的洞察:麻醉诱导的神经元网络活动的瓦解@2018-10-25 11:40:00; 3:结合网络科学与神经影像技术揭示大脑和意识中大规模@2018-10-25 15:00:00
- http://em.scnu.edu.cn/a/20180615/6407.html（旧1→新2） 新场样例: 1:房价是如何影响流动人口定居意愿的 ?@2018-06-15 14:00:00; 2:城市与区域发展专题学术报告研讨会@2018-06-15 15:30:00
- http://em.scnu.edu.cn/a/20180106/6083.html（旧1→新9） 新场样例: 1:Do birthdates matter for@2018-01-06 08:30:00; 2:数字化时代大学图书馆的发展方向@2018-01-06 09:00:00; 3:大数据时代图书馆用户移动信息行为探析@2018-01-06 09:30:00
- http://em.scnu.edu.cn/a/20180105/6075.html（旧1→新2） 新场样例: 1:Endogenous Horizontal Pr@2018-01-05 14:00:00; 2:Comparison of energy eff@2018-01-05 15:30:00
- http://psy.scnu.edu.cn/a/20171113/1380.html（旧1→新2） 新场样例: 1::00-9:40成立仪式及揭牌@2017-11-16 09:40:00; 2:The Challenge of General@2017-11-16 15:50:00
- http://em.scnu.edu.cn/a/20171104/5916.html（旧1→新6） 新场样例: 1:主持人介绍活动安排及嘉宾@2017-11-04 09:00:00; 2:嘉宾交流互动@2017-11-04 09:00:00; 3:主题演讲:财务自动化(陈琳)@2017-11-04 09:10:00
- http://em.scnu.edu.cn/a/20170324/5493.html（旧1→新2） 新场样例: 1:领导者与风险偏好@2017-03-24 14:30:00; 2:To what extent does bank@2017-03-24 16:00:00
- http://em.scnu.edu.cn/a/20170107/5388.html（旧1→新7） 新场样例: 1:《分权制衡与司法独立:来自公安厅厅长兼任政法委书@2017-01-07 08:30:00; 2:《分权制衡与司法独立：来自公安@2017-01-07 08:30:00; 3:《留守儿童与非政策性户籍歧视》@2017-01-07 09:05:00
- http://psy.scnu.edu.cn/a/20161212/1201.html（旧1→新10） 新场样例: 1:睡眠缺失的神经机制@2016-12-14 10:10:00; 2:Self – Other Differences@2016-12-14 10:35:00; 3:走进在残酷青春中努力挣扎的自伤者@2016-12-14 11:00:00
- http://psy.scnu.edu.cn/a/20161212/1199.html（旧1→新3） 新场样例: 1:自闭症研究现状、进步与挑战@2016-12-13 14:30:00; 2:应用行为分析与自闭症谱系障碍@2016-12-13 14:30:00; 3:自闭症融合支持的研究现状和趋势@2016-12-13 14:30:00
- http://em.scnu.edu.cn/a/20161208/5295.html（旧1→新2） 新场样例: 1:Identity in Public Good @2016-12-08 14:00:00; 2:E-Commerce Integration a@2016-12-08 15:30:00
- http://psy.scnu.edu.cn/a/20161114/1144.html（旧1→新3） 新场样例: 1:介绍参会嘉宾、 揭牌仪式、嘉宾致词@2016-11-18 03:00:00; 2:唐向东教授学术报告会@2016-11-18 03:20:00; 3:贾福军教授学术报告会@2016-11-18 04:30:00
- http://em.scnu.edu.cn/a/20151024/4031.html（旧1→新6） 新场样例: 1:非正规就业使大学毕业生陷入低收入陷阱了吗？@2015-10-24 09:00:00; 2:共情陪伴与留守儿童精神健康研究@2015-10-24 10:00:00; 3:中国工会还做什么？@2015-10-24 11:00:00
- http://em.scnu.edu.cn/a/20150509/3993.html（旧1→新10） 新场样例: 1:城市农民工 " 地缘集聚 " 现象——基于社会网@2015-05-09 09:10:00; 2:我国城镇化要推进到什么程度:基于国际证据@2015-05-09 09:40:00; 3:流动人口往老家汇款仅仅是利他吗？@2015-05-09 10:10:00
- http://psy.scnu.edu.cn/a/20150320/329.html（旧1→新2） 新场样例: 1:习得的刺激-反应联结调节认知控制的神经基础@2015-03-24 16:00:00; 2:习得的刺激-反应联结调节认知控制的神经基础@2015-03-24 16:00:00
- http://psy.scnu.edu.cn/a/20141124/311.html（旧1→新2） 新场样例: 1:不孕症与压力@2014-11-25 02:45:00; 2:失眠的神经行为病因模式@2014-11-25 04:20:00
- http://psy.scnu.edu.cn/a/20131129/221.html（旧1→新3） 新场样例: 1:统计与方法学的神话:青少年问题研究中的教条与真相@2013-12-01 08:30:00; 2:识别发展的个体差异:潜在类别 / 剖面分析与混合@2013-12-01 14:30:00; 3:Mplus 基本应用@2013-12-01 19:00:00
- http://psy.scnu.edu.cn/a/20130510/172.html（旧1→新2） 新场样例: 1:台湾临床心理学的过去、现在和未来@2013-05-13 15:00:00; 2:社会焦虑的心理病理机制与疗效探讨@2013-05-14 15:00:00

## split_count_change（6 页）

- http://cs.scnu.edu.cn/a/20230315/5400.html（旧4→新3） 新场样例: 1:人工智能视野下的科技伦理教育@2023-03-18 10:00:00; 2:资源分配问题的模型与算法@2023-03-18 10:40:00; 3:从教学学术视角谈一流课程建设与实践@2023-03-18 11:20:00
- http://em.scnu.edu.cn/a/20200108/7635.html ⚠manual（旧6→新5） 新场样例: 1:图书馆营销的理论基础及研究内容@2020-01-08 09:45:00; 2:《公共图书馆业务规范》:从标准到指南@2020-01-08 10:00:00; 3:我国大数据交易中数据匿名的规制现状与对策@2020-01-08 10:25:00
- http://em.scnu.edu.cn/a/20171202/5983.html ⚠manual（旧32→新25） 新场样例: 1:新农合改善了农村老年人健康吗――基于Multil@2017-12-02 12:30:00; 2:"互联网+"背景下佛山市双创服务发展思路与对策研@2017-12-02 12:30:00; 3:"互联网+医疗"对老年人健康与医疗消费的作用机制@2017-12-02 12:30:00
- http://em.scnu.edu.cn/a/20171118/5949.html ⚠manual（旧12→新18） 新场样例: 1:股市群体投资行为函数的构建和实证@2017-11-18 13:30:00; 2:基于 Agent-based model 的不良@2017-11-18 13:30:00; 3:高管生肖对企业价值有影响吗？@2017-11-18 13:30:00
- http://em.scnu.edu.cn/a/20150104/3769.html（旧6→新7） 新场样例: 1:公共信息服务与社会发展@2015-01-10 09:00:00; 2:公共图书馆信息服务创新研究@2015-01-10 09:25:00; 3:开放式创新中知识产权协作机制研究@2015-01-10 09:45:00
- http://em.scnu.edu.cn/a/20190509/6973.html ⚠manual（旧7→新3） 新场样例: 1:Peer Effects on Student @2019-05-09 13:00:00; 2:" 一带一路 " 倡议影响公司债发行定价吗？ —@2019-05-09 15:20:00; 3:How do temperature extre@2019-05-09 16:50:00

## split_reverted（32 页）

- https://physics.scnu.edu.cn/a/20260602/13312.html（旧2→新1） 旧场样例: 2:AI 辅助研究非线性系统:构造广义李雅普诺夫函数; 1:非线性动力系统的广义李雅普诺夫函数
- http://psy.scnu.edu.cn/a/20260324/2940.html（旧2→新1） 旧场样例: 1:Uncertainty and compress; 2:Neural Mechanisms of Emo
- http://aol.scnu.edu.cn/a/20251203/1385.html（旧4→新1） 旧场样例: 4:作为一种刺激物“设计”对人带来的诸多关键影响; 3:AI浪潮下的美术教育; 2:绘画表达间的“差异”
- http://xz.scnu.edu.cn/a/20251114/301.html（旧2→新1） 旧场样例: 1:集成光子学; 2:超快光学技术和应用
- http://lswh.scnu.edu.cn/a/20251013/65.html（旧4→新1） 旧场样例: 4:历史学的基本概念与史家的基本素养; 3:世界主要文明体对历史与史学的看法; 2:中国马克思主义史学的来龙去脉
- http://xz.scnu.edu.cn/a/20250909/289.html（旧4→新0） 旧场样例: 4:“挑战杯”学生创业计划竞赛（小挑）赛事解读与备赛; 3:“挑战杯”学生创业计划竞赛（小挑）赛事解读与备赛; 2:“挑战杯”学生创业计划竞赛（小挑）赛事解读与备赛
- http://xz.scnu.edu.cn/a/20250109/256.html（旧3→新1） 旧场样例: 1:语众不同，跨界碰撞——从创业到创赛经验分享; 2:化念成行，行则将至——深圳教师编上岸经验分享; 3:书山有路，学以致用——留学上岸经验分享
- http://ctld.scnu.edu.cn/a/20241021/4381.html（旧4→新1） 旧场样例: 4:基于生成式人工智能的外语教学活动设计; 3:人工智能赋能语言研究的理论与实践; 2:生成式人工智能与外语教学变革
- http://jky.scnu.edu.cn/a/20241029/5920.html（旧2→新1） 旧场样例: 1:; 2:
- http://em.scnu.edu.cn/a/20241011/10290.html（旧2→新1） 旧场样例: 1:经济理论与实验的关系; 2:"扣除率"越高合作率越高:关于公共品提供的理论与
- http://em.scnu.edu.cn/a/20240628/10191.html（旧2→新1） 旧场样例: 1:职业经验交流、投资策略; 2:风险管理经验分享
- http://xz.scnu.edu.cn/a/20240511/207.html（旧3→新1） 旧场样例: 1:纳米铂稀土合金的制备及氧还原性能表征; 2:碱金属碳酸盐协同碱/碱土金属硅酸盐捕获二氧化碳行; 3:铝空气电池铝合金阳极制备
- http://xz.scnu.edu.cn/a/20240116/127.html（旧3→新1） 旧场样例: 3:设计思维与创新能力; 2:创新创业竞赛项目的选择; 1:创新创业竞赛与学生成长
- http://swc.scnu.edu.cn/collaborative/2024/0101/56.html（旧3→新1） 旧场样例: 3:基于智慧高速“云-网-节-端”平台的视觉数据分析; 2:可信安全的自动驾驶系统; 1:具身智能技术方法与应用和挑战
- http://swc.scnu.edu.cn/collaborative/2023/1225/54.html（旧2→新0） 旧场样例: 1:教师成长的加速器：教学研究; 2:用极简技术让课堂有点酷
- http://xz.scnu.edu.cn/a/20231023/105.html（旧3→新1） 旧场样例: 3:第3讲丨高校学生工作的原则和方法; 2:第2讲丨谈谈华南师范大学学生工作的优良传统; 1:第1讲丨谈谈学生干部的素质修养
- http://abdn.scnu.edu.cn/a/20230504/362.html（旧3→新1） 旧场样例: 1:学者讲坛第7讲丨A Brief Introduc; 2:学者讲坛第8讲丨Co-creating Luxu; 3:学者讲坛第9讲丨Deep Learning fo
- http://gxb.scnu.edu.cn/a/20220930/615.html（旧2→新1） 旧场样例: 1:高校科技成果转化案例分享; 2:佛山市人才团队政策宣讲
- http://xz.scnu.edu.cn/a/20221026/65.html（旧5→新1） 旧场样例: 1:低速自动驾驶产业化和未来-以城市之光为例; 2:利用对抗生成网络进行数据增强的端到端语音情感识别; 3:基于双通道模型提取互补特征的语音情感识别
- http://xz.scnu.edu.cn/a/20220924/56.html（旧2→新1） 旧场样例: 1:结构光三维重建概述; 2:大气颗粒物采样技术
- http://xz.scnu.edu.cn/a/20220917/51.html（旧3→新1） 旧场样例: 1:语音当中的情感识别及其应用; 2:情感计算中人脸表情识别的技术方法及应用; 3:基于深度学习的2D目标检测技术方法及其应用
- http://swc.scnu.edu.cn/collaborative/2021/0705/34.html（旧3→新1） 旧场样例: 3:服装设计表达——从效果图到成衣; 2:本科教学中几个问题; 1:电化学分析导论
- http://swc.scnu.edu.cn/collaborative/2021/0628/33.html（旧5→新1） 旧场样例: 4:省级工业设计平台建设与实践; 5:《机床电气控制》课程教学组织; 3:跨境电商发展研究
- http://swc.scnu.edu.cn/collaborative/2021/0621/32.html（旧5→新1） 旧场样例: 4:省级工业设计平台建设与实践; 5:《机床电气控制》课程教学组织; 3:金融科技发展
- http://swc.scnu.edu.cn/collaborative/2021/0615/31.html（旧3→新1） 旧场样例: 3:信息化社会环境下时尚设计与设计教育发展路径的嬗变; 2:数字经济发展; 1:刚体力学基础
- http://swc.scnu.edu.cn/collaborative/2021/0608/30.html（旧5→新1） 旧场样例: 4:工业设计专业建设与实践; 5:可靠性冗余分配优化研究; 3:我国公司治理的热点问题
- http://cs.scnu.edu.cn/a/20190709/4145.html（旧3→新1） 旧场样例: 1:人工智能、大数据、教育科学论坛; 2:人工智能、大数据、教育科学论坛; 3:人工智能、大数据、教育科学论坛
- http://sfs.scnu.edu.cn/a/20190520/2359.html（旧4→新1） 旧场样例: 3:Understanding teacher as; 4:Unpacking educator expec; 1:Understanding teachers' 
- http://em.scnu.edu.cn/a/20190112/6769.html ⚠manual（旧18→新1） 旧场样例: 1:"一带一路"倡议有助于降低企业融资约束吗？—一个; 2:万达电影"封杀"了华谊兄弟吗？——对检验"纵向一; 3:李约瑟之谜新解——来自通商开埠与科举的经验证据
- http://geography.scnu.edu.cn/a/20180609/45.html（旧3→新1） 旧场样例: 1:全球变化下河流碳输送及界面CO2释放; 2:Ferdinand von Richthofen; 3:河流环境演变效应评估：流域文明与人类社会发展
- http://geography.scnu.edu.cn/a/20161222/12.html（旧7→新1） 旧场样例: 1:Effects of urban geometr; 2:基于博弈论的快速城市化地区生态控制线划定; 3:Possibilities and proble
- http://psy.scnu.edu.cn/a/20130701/192.html ⚠manual（旧2→新1） 旧场样例: 1:Brain Responses to Const; 2:Sleep and youth suicidal

## parse_empty（44 页）

- http://xz.scnu.edu.cn/a/20260312/318.html（旧1→新0）
- http://ctld.scnu.edu.cn/a/20251107/4476.html（旧1→新0）
- http://ibc.scnu.edu.cn/a/20250107/2779.html（旧1→新0）
- http://xz.scnu.edu.cn/a/20231205/116.html（旧1→新0）
- http://xz.scnu.edu.cn/a/20230505/94.html（旧1→新0）
- http://xz.scnu.edu.cn/a/20230329/87.html（旧1→新0）
- http://xz.scnu.edu.cn/a/20230328/86.html（旧1→新0）
- http://ctld.scnu.edu.cn/a/20221128/4106.html（旧1→新0）
- http://xz.scnu.edu.cn/a/20221210/78.html（旧1→新0）
- http://xz.scnu.edu.cn/a/20221117/70.html（旧1→新0）
- http://xz.scnu.edu.cn/a/20221027/66.html（旧1→新0）
- http://xz.scnu.edu.cn/a/20220924/60.html（旧1→新0）
- http://swc.scnu.edu.cn/collaborative/2021/1105/35.html（旧1→新0）
- http://gxb.scnu.edu.cn/a/20211008/22.html（旧1→新0）
- https://physics.scnu.edu.cn/a/20210603/11634.html（旧1→新0）
- http://ctld.scnu.edu.cn/a/20210517/1365.html（旧1→新0）
- http://ctld.scnu.edu.cn/a/20210422/1341.html（旧1→新0）
- http://ctld.scnu.edu.cn/a/20210311/1299.html（旧1→新0）
- http://ctld.scnu.edu.cn/a/20201218/1278.html（旧1→新0）
- http://ctld.scnu.edu.cn/a/20201127/1268.html（旧1→新0）
- http://ctld.scnu.edu.cn/a/20201113/1250.html（旧1→新0）
- http://ctld.scnu.edu.cn/a/20201016/1234.html（旧1→新0）
- http://ctld.scnu.edu.cn/a/20191220/1093.html（旧1→新0）
- http://geography.scnu.edu.cn/a/20191214/96.html（旧1→新0）
- http://geography.scnu.edu.cn/a/20191214/92.html（旧1→新0）
- http://ctld.scnu.edu.cn/a/20191125/1076.html（旧1→新0）
- http://ctld.scnu.edu.cn/a/20191022/1042.html（旧1→新0）
- http://ctld.scnu.edu.cn/a/20190920/1027.html（旧1→新0）
- http://geography.scnu.edu.cn/a/20190702/75.html（旧1→新0）
- http://ctld.scnu.edu.cn/a/20190619/998.html（旧1→新0）
- http://ctld.scnu.edu.cn/a/20190527/986.html（旧1→新0）
- http://ctld.scnu.edu.cn/a/20190428/968.html（旧1→新0）
- http://geography.scnu.edu.cn/a/20190329/61.html（旧1→新0）
- http://ctld.scnu.edu.cn/a/20190322/940.html（旧1→新0）
- http://ctld.scnu.edu.cn/a/20181126/892.html（旧1→新0）
- http://geography.scnu.edu.cn/a/20181119/55.html（旧1→新0）
- http://geography.scnu.edu.cn/a/20181111/54.html（旧1→新0）
- http://ctld.scnu.edu.cn/a/20180620/841.html（旧1→新0）
- http://ctld.scnu.edu.cn/a/20180525/704.html（旧1→新0）
- http://ctld.scnu.edu.cn/a/20180423/592.html（旧1→新0）
- https://lib.scnu.edu.cn/news/zuixingonggao/2018/0419/180.html（旧1→新0）
- http://psy.scnu.edu.cn/a/20170624/1322.html（旧1→新0）
- https://physics.scnu.edu.cn/a/20151117/797.html（旧1→新0）
- http://psy.scnu.edu.cn/a/20151204/903.html（旧1→新0）

## field_diff（1540 页，样例前 60 页）

- https://module.scnu.edu.cn/article-3685-11163-1.html（可行动 1 处）#1 speakerAffiliation: 广发期货 → 
- https://module.scnu.edu.cn/article-3685-11164-1.html（可行动 1 处）#1 speakerAffiliation: 暨南大学 → 
- https://module.scnu.edu.cn/article-3685-11162-1.html（可行动 1 处）#1 speakerAffiliation: 广发期货 → 
- http://maths.scnu.edu.cn/a/20260912/8837.html（可行动 1 处）#1 speakerAffiliation: 浙江师范大学数学科学学院 → 
- https://physics.scnu.edu.cn/a/20260821/13344.html（可行动 1 处）#1 topic: 高品质量子光源的制备、检测与应用 → 高品质量子光源的制备、探测与应用
- http://maths.scnu.edu.cn/a/20260804/8809.html（可行动 1 处）#1 speakerAffiliation: 苏州大学 → 
- http://maths.scnu.edu.cn/a/20260804/8806.html（可行动 1 处）#1 speakerAffiliation: 中南大学 → 中南大学数学与统计学院
- http://maths.scnu.edu.cn/a/20260804/8808.html（可行动 1 处）#1 speakerAffiliation: 华中科技大学数学与统计学院 → 
- http://maths.scnu.edu.cn/a/20260804/8805.html（可行动 1 处）#1 speakerAffiliation: 上海师范大学 → 上海师范大学数学系
- http://maths.scnu.edu.cn/a/20260802/8801.html（可行动 1 处）#1 speakerAffiliation: 大连理工大学数学科学学院 → 
- http://maths.scnu.edu.cn/a/20260804/8804.html（可行动 1 处）#1 speakerAffiliation: 哈尔滨工业大学 → 
- https://maths.scnu.edu.cn/a/20260802/8803.html（可行动 1 处）#1 speakerAffiliation: 南方科技大学 → 
- http://maths.scnu.edu.cn/a/20260717/8794.html（可行动 1 处）#1 speakerAffiliation: 北京雁栖湖应用数学研究院 → 
- http://maths.scnu.edu.cn/a/20260729/8800.html（可行动 1 处）#1 speakerAffiliation: 香港理工大学 → 
- http://maths.scnu.edu.cn/a/20260713/8791.html（可行动 1 处）#1 speakerAffiliation: 广州大学 → 
- http://maths.scnu.edu.cn/a/20260708/8778.html（可行动 1 处）#1 speakerAffiliation: 湖南大学数学学院 → 
- http://maths.scnu.edu.cn/a/20260708/8792.html（可行动 1 处）#1 speakerAffiliation: 广东金融学院 → 
- https://physics.scnu.edu.cn/a/20260703/13328.html（可行动 1 处）#1 location: 理 6 栋 302 → 理6栋302
- http://psy.scnu.edu.cn/a/20260707/2990.html（可行动 1 处）#1 lectureEnd: 2026-07-09 18:00:00 → 2026-07-09 12:00:00
- http://maths.scnu.edu.cn/a/20260706/8777.html（可行动 1 处）#1 speakerAffiliation: 中山大学 → 中山大学数学学院
- http://maths.scnu.edu.cn/a/20260706/8776.html（可行动 1 处）#1 speakerAffiliation: 华南理工大学 → 
- https://physics.scnu.edu.cn/a/20260703/13327.html（可行动 2 处）#1 topic: 成为研究型教师：科学教育的选题、方法与路径 → 成为研究型教师:科学教育研究的选题、方法与路径; #1 speakerAffiliation: 浙江大学教育学院 → 浙江大学
- http://maths.scnu.edu.cn/a/20260701/8775.html（可行动 1 处）#1 speakerAffiliation: 武汉大学 → 
- http://seri.scnu.edu.cn/Resources_CN/xueshujiangzuo/2026/0628/74.html（可行动 2 处）#1 topic:  → Using machine learning to predict the effects of p; #1 speakerAffiliation: 荷兰瓦格宁根大学水生生态和水质管理组 → 是荷兰瓦格宁根大学
- http://maths.scnu.edu.cn/a/20260630/8760.html（可行动 1 处）#1 speakerAffiliation: 美国布朗大学 → 
- http://maths.scnu.edu.cn/a/20260629/8748.html（可行动 1 处）#1 speakerAffiliation: Loyola University Maryland → 
- http://maths.scnu.edu.cn/a/20260623/8759.html（可行动 1 处）#1 speakerAffiliation: 法国索邦大学 → 
- http://skc.scnu.edu.cn/a/20260616/1095.html（可行动 1 处）#1 location: 第二教学楼(研究生院) 210 会议厅 → 第二教学楼(研究生院)210会议厅
- http://maths.scnu.edu.cn/a/20260618/8758.html（可行动 1 处）#1 speakerAffiliation: 复旦大学 → 
- http://maths.scnu.edu.cn/a/20260615/8725.html（可行动 1 处）#1 speakerAffiliation: 澳门大学 → 
- http://maths.scnu.edu.cn/a/20260615/8743.html（可行动 1 处）#1 speakerAffiliation: 华中师范大学 → 
- http://maths.scnu.edu.cn/a/20260614/8757.html（可行动 1 处）#1 speakerAffiliation: 大连理工大学 → 
- http://maths.scnu.edu.cn/a/20260614/8716.html（可行动 1 处）#1 speakerAffiliation: 南开大学 → 中国科学院
- http://maths.scnu.edu.cn/a/20260612/8742.html（可行动 1 处）#1 speakerAffiliation: 对外经济贸易大学 → 
- http://life.scnu.edu.cn/a/20260617/6771.html（可行动 3 处）#1 speaker: Kirst King-Jones → Ecdysone Axis; #1 speakerAffiliation: 阿尔伯塔大学 → in Drosophila Development; #1 location: 生命科学学院102会议室 → 生命科学学院102会议室华南年轮大学華南师毛大学SOUTHCHINANORMALUNIVERSITY
- http://psy.scnu.edu.cn/a/20260613/2981.html（可行动 2 处）#1 topic: Gastrophysics: The new science of eating → Charles Spence; #1 speakerAffiliation: 牛津大学实验心理学 → 教授学术
- http://iqm.scnu.edu.cn/a/20260608/546.html（可行动 2 处）#1 topic: 宇宙线能谱的高精度测量：LHAASO 的成果与展望 → 宇宙线能谱的高精度测量: LHAASO 的成果与展望; #1 location: 理8楼118学术报告厅 → 理8栋118学术报告厅
- https://physics.scnu.edu.cn/a/20260609/13317.html（可行动 1 处）#1 topic: 二维晶体非线性光学研究报告 → 二维晶体非线性光学研究
- http://maths.scnu.edu.cn/a/20260605/8747.html（可行动 1 处）#1 speakerAffiliation: Purdue University Fort Wayne → 普渡大学
- http://maths.scnu.edu.cn/a/20260605/8740.html（可行动 1 处）#1 speakerAffiliation: 中国矿业大学 → 
- https://physics.scnu.edu.cn/a/20260602/13310.html（可行动 1 处）#1 location: 理 6 栋 302 → 理6栋302
- http://psy.scnu.edu.cn/a/20260608/2978.html（可行动 3 处）#1 topic: A lexicalist view of syntactic representations in  → Robert J. Hartsuiker; #1 speakerAffiliation: Department of Experimental Psychology, Ghent Unive → 教授学术; #1 location: 214报告厅 华师心理学院 → 
- http://life.scnu.edu.cn/a/20260603/6758.html（可行动 2 处）#1 speakerAffiliation: 广州医科大学附属妇女儿童医疗中心 → 广州医科大学; #1 location: 生命科学学院102会议室 → 生命科学学院102会议室华南年轮大学華南师花大学SOUTHCHINANORMALUNIVERSITY
- http://skc.scnu.edu.cn/a/20260529/1076.html（可行动 1 处）#1 topic: 以格局铸根基,以传统文化育人才 → 以格局铸根基以传统文化育人才
- http://maths.scnu.edu.cn/a/20260530/8739.html（可行动 1 处）#1 speakerAffiliation: 重庆理工大学数学科学研究中心 → 重庆理工大学
- http://maths.scnu.edu.cn/a/20260529/8734.html（可行动 1 处）#1 speakerAffiliation: 清华大学 → 
- http://maths.scnu.edu.cn/a/20260529/8724.html（可行动 1 处）#1 speakerAffiliation: 湖南师范大学 → 
- https://physics.scnu.edu.cn/a/20260527/13301.html（可行动 2 处）#1 speaker: 常钰婷 → 常钰婷华; #1 speakerAffiliation: 华中科技大学国家脉冲强磁场科学中心 → 中科技大学
- http://iqm.scnu.edu.cn/a/20260525/539.html（可行动 2 处）#1 speaker: Da Yu Tou → High Throughput Software Trigger; #1 speakerAffiliation: Tsinghua University → 
- http://maths.scnu.edu.cn/a/20260525/8730.html（可行动 1 处）#1 speakerAffiliation: 埃因霍温理工大学 → 
- https://physics.scnu.edu.cn/a/20260518/13296.html（可行动 1 处）#1 speakerAffiliation: 中国科学院精密测量科学与技术创新研究院 → 中国科学院
- http://maths.scnu.edu.cn/a/20260523/8729.html（可行动 2 处）#1 topic: Eigenvalue estimates for Laplacians on f-minimal h → Eigenvalue estimates for Laplacians on f-$f$-minim; #1 speakerAffiliation: Universidade Federal Fluminense → Fluminense联邦大学
- http://maths.scnu.edu.cn/a/20260526/8728.html（可行动 1 处）#1 speakerAffiliation: 西湖大学理论科学研究院 → 
- http://skc.scnu.edu.cn/a/20260518/1071.html（可行动 1 处）#1 location: 石牌校区文科楼二层 211 讲堂 → 石牌校园文科楼二楼211讲学厅
- http://psy.scnu.edu.cn/a/20260520/2957.html（可行动 1 处）#1 topic: 当孩子带着喜悦回家：青少年积极情绪的表达与父母回应 → 当孩子带着喜悦回家:青少年积极情绪的分享与父母回应
- https://physics.scnu.edu.cn/a/20260519/13298.html（可行动 1 处）#1 speakerAffiliation: 深圳理工大学 → 
- http://maths.scnu.edu.cn/a/20260520/8723.html（可行动 1 处）#1 speakerAffiliation: Central Connecticut State University → 
- https://physics.scnu.edu.cn/a/20260515/13295.html（可行动 2 处）#1 topic: 量子开放系统中的非平衡周期振荡：时间晶体与量子同步 → 量子开放系统中的非平衡周期振荡:时间晶体与量子同步; #1 lectureStart: 2026-05-18 15:30:00 → 2026-05-18 00:00:00
- http://maths.scnu.edu.cn/a/20260517/8711.html（可行动 1 处）#1 speakerAffiliation: 汕头大学 → 
- http://maths.scnu.edu.cn/a/20260515/8717.html（可行动 1 处）#1 speakerAffiliation: 北京工业大学 → 