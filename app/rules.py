"""领域规则数据：技能词典、岗位画像、院校层级、章节别名。

这里只放"知识"，不放逻辑；所有阈值/权重均可在 config.yaml 中覆盖。
"""

# 章节标题别名（用于从简历原文切分结构）
SECTION_ALIASES = {
    "education": ["教育背景", "教育经历", "学历背景", "学习经历", "教育"],
    "experience": ["工作经历", "工作经验", "职业经历", "工作履历", "实习经历", "实习经验", "职业背景"],
    "projects": ["项目经历", "项目经验", "主要项目", "项目实践", "代表项目", "核心项目", "项目"],
    "skills": ["专业技能", "技能特长", "技术栈", "技能清单", "个人技能", "技术能力", "技能"],
    "summary": ["个人总结", "自我评价", "个人简介", "个人优势", "自我介绍", "个人评价", "简介"],
    "awards": ["荣誉奖项", "获奖情况", "证书资质", "资格证书", "荣誉", "奖项"],
}

SECTION_LABELS = {
    "education": "教育背景",
    "experience": "工作经历",
    "projects": "项目经历",
    "skills": "专业技能",
    "summary": "个人总结",
    "awards": "荣誉证书",
}

# 技能词典：类别 -> {规范名: [别名...]}（别名一律小写，英文匹配忽略大小写）
SKILL_CATALOG = {
    "编程语言": {
        "Python": ["python"],
        "Java": ["java"],
        "JavaScript": ["javascript", "js"],
        "TypeScript": ["typescript", "ts"],
        "Go": ["golang", "go语言"],
        "C++": ["c++", "cpp"],
        "C#": ["c#", "csharp"],
        "C": ["c语言"],
        "Rust": ["rust"],
        "PHP": ["php"],
        "Kotlin": ["kotlin"],
        "Swift": ["swift"],
        "Scala": ["scala"],
        "SQL": ["sql"],
    },
    "后端与架构": {
        "Spring Boot": ["spring boot", "springboot", "spring-boot"],
        "Spring Cloud": ["spring cloud", "springcloud"],
        "Django": ["django"],
        "Flask": ["flask"],
        "FastAPI": ["fastapi"],
        "Express": ["express", "express.js"],
        "NestJS": ["nestjs"],
        "Gin": ["gin框架", "gin"],
        "微服务": ["微服务", "microservice", "micro-services"],
        "分布式": ["分布式", "distributed"],
        "高并发": ["高并发", "高吞吐", "并发量"],
        "消息队列": ["消息队列", "mq", "rabbitmq", "rocketmq", "activemq"],
        "RESTful API": ["restful", "rest api", "restful api"],
        "gRPC": ["grpc"],
    },
    "前端": {
        "React": ["react", "react.js"],
        "Vue": ["vue", "vue.js", "vue3"],
        "Angular": ["angular"],
        "Next.js": ["next.js", "nextjs"],
        "Webpack": ["webpack"],
        "Vite": ["vite"],
        "HTML/CSS": ["html", "css", "html5", "css3"],
        "Tailwind": ["tailwind", "tailwindcss"],
        "小程序": ["小程序", "微信小程序", "uniapp"],
        "Three.js": ["three.js", "threejs"],
    },
    "数据库": {
        "MySQL": ["mysql"],
        "PostgreSQL": ["postgresql", "postgres", "pgsql"],
        "Redis": ["redis"],
        "MongoDB": ["mongodb", "mongo"],
        "Oracle": ["oracle"],
        "SQL Server": ["sql server", "sqlserver"],
        "Elasticsearch": ["elasticsearch", "elastic search"],
        "ClickHouse": ["clickhouse"],
        "SQLite": ["sqlite"],
    },
    "云与运维": {
        "Docker": ["docker", "容器化"],
        "Kubernetes": ["kubernetes", "k8s"],
        "AWS": ["aws", "amazon web services"],
        "Azure": ["azure"],
        "阿里云": ["阿里云", "aliyun", "ali cloud"],
        "腾讯云": ["腾讯云", "tencent cloud"],
        "Linux": ["linux", "centos", "ubuntu"],
        "Nginx": ["nginx"],
        "CI/CD": ["ci/cd", "cicd", "持续集成", "持续交付"],
        "Jenkins": ["jenkins"],
        "Git": ["git", "github", "gitlab"],
        "Terraform": ["terraform"],
        "Prometheus": ["prometheus"],
    },
    "数据与 AI": {
        "Spark": ["spark", "pyspark"],
        "Hadoop": ["hadoop"],
        "Flink": ["flink"],
        "Kafka": ["kafka"],
        "Hive": ["hive"],
        "Airflow": ["airflow"],
        "TensorFlow": ["tensorflow"],
        "PyTorch": ["pytorch"],
        "Pandas": ["pandas", "numpy"],
        "机器学习": ["机器学习", "machine learning", "ml"],
        "深度学习": ["深度学习", "deep learning", "神经网络"],
        "大模型": ["大模型", "llm", "gpt", "transformer", "大语言模型"],
        "RAG": ["rag", "检索增强"],
        "推荐系统": ["推荐系统", "推荐算法", "召回", "排序模型"],
        "计算机视觉": ["计算机视觉", "cv", "图像识别", "opencv"],
        "NLP": ["nlp", "自然语言处理"],
        "数据分析": ["数据分析", "数据挖掘", "bi", "tableau", "power bi"],
    },
    "产品与管理": {
        "项目管理": ["项目管理", "project management"],
        "需求分析": ["需求分析", "需求调研", "prd"],
        "敏捷开发": ["敏捷", "scrum", "agile"],
        "团队管理": ["团队管理", "带团队", "管理团队", "leader"],
        "用户增长": ["用户增长", "增长黑客", "拉新", "留存"],
        "数据分析能力": ["数据驱动", "指标体系", "ab test", "a/b"],
        "OKR": ["okr", "kpi"],
        "沟通协作": ["沟通", "协作", "跨部门"],
    },
    "测试": {
        "自动化测试": ["自动化测试", "selenium", "pytest", "appium", "ui automation"],
        "性能测试": ["性能测试", "jmeter", "loadrunner", "压测"],
        "接口测试": ["接口测试", "postman", "api testing"],
        "测试用例": ["测试用例", "测试计划", "缺陷管理"],
    },
    "证书": {
        "PMP": ["pmp"],
        "软考": ["软考", "系统架构师", "信息系统项目管理师"],
        "CET-6": ["cet-6", "cet6", "英语六级", "六级"],
        "雅思": ["雅思", "ielts"],
        "托福": ["托福", "toefl"],
        "CPA": ["cpa", "注册会计师"],
        "CFA": ["cfa"],
    },
}

# 额外岗位相关词（不一定属于技能词典，但需要参与命中匹配）
EXTRA_TERMS = {
    "微服务": ["微服务", "microservice", "micro-services"],
    "分布式": ["分布式", "distributed"],
    "高并发": ["高并发", "高吞吐", "并发量"],
    "消息队列": ["消息队列", "mq", "rabbitmq", "rocketmq", "activemq"],
    "性能优化": ["性能优化", "性能调优", "优化性能", "降本提效"],
    "架构设计": ["架构设计", "系统设计", "技术方案", "领域驱动", "ddd"],
    "代码评审": ["代码评审", "code review", "cr"],
    "单元测试": ["单元测试", "unit test", "tdd"],
    "容器化": ["容器化", "docker", "k8s"],
    "数据治理": ["数据治理", "数据仓库", "数仓", "etl"],
    "可视化": ["可视化", "大屏", "echarts", "d3"],
    "埋点": ["埋点", "数据采集", "日志采集"],
    "国际化": ["国际化", "i18n", "多语言"],
    "安全": ["安全", "渗透测试", "等保", "加密"],
}

# 岗位画像：关键词 -> 权重（1-3）
ROLE_PROFILES = {
    "backend": {
        "label": "后端开发",
        "keywords": {
            "Python": 3, "Java": 3, "Go": 2.5, "Spring Boot": 3, "Spring Cloud": 2,
            "Django": 2, "FastAPI": 2.5, "MySQL": 3, "Redis": 2.5, "Kafka": 2,
            "Docker": 2, "Kubernetes": 2, "微服务": 2.5, "分布式": 2.5,
            "高并发": 2, "消息队列": 2, "Linux": 1.5, "RESTful API": 2, "gRPC": 1.5,
        },
    },
    "frontend": {
        "label": "前端开发",
        "keywords": {
            "JavaScript": 3, "TypeScript": 2.5, "React": 3, "Vue": 3,
            "HTML/CSS": 2.5, "Webpack": 2, "Vite": 1.5, "Next.js": 2,
            "小程序": 2, "Node.js": 1.5, "性能优化": 2, "可视化": 2, "Tailwind": 1.5,
        },
    },
    "fullstack": {
        "label": "全栈开发",
        "keywords": {
            "Python": 2.5, "Java": 2, "JavaScript": 2.5, "TypeScript": 2,
            "React": 2.5, "Vue": 2, "Spring Boot": 2, "MySQL": 2.5, "Redis": 2,
            "Docker": 2, "RESTful API": 2, "Linux": 1.5, "微服务": 1.5,
        },
    },
    "data": {
        "label": "数据分析",
        "keywords": {
            "SQL": 3, "Python": 3, "Pandas": 2.5, "数据分析": 3, "数据治理": 2.5,
            "Hive": 2, "Spark": 2.5, "ClickHouse": 2, "Tableau": 2, "Power BI": 2,
            "指标体系": 2, "埋点": 1.5, "Airflow": 1.5, "机器学习": 1.5,
        },
    },
    "algorithm": {
        "label": "算法工程师",
        "keywords": {
            "Python": 3, "PyTorch": 3, "TensorFlow": 2.5, "机器学习": 3,
            "深度学习": 3, "大模型": 2.5, "RAG": 2, "推荐系统": 2.5,
            "NLP": 2.5, "计算机视觉": 2.5, "SQL": 2, "Spark": 1.5,
        },
    },
    "product": {
        "label": "产品经理",
        "keywords": {
            "需求分析": 3, "项目管理": 2.5, "用户增长": 2.5, "数据分析能力": 2,
            "敏捷开发": 2, "沟通协作": 2, "OKR": 1.5, "数据分析": 2, "PRD": 1.5,
        },
    },
    "test": {
        "label": "测试工程师",
        "keywords": {
            "自动化测试": 3, "接口测试": 2.5, "性能测试": 2.5, "测试用例": 2.5,
            "Python": 2.5, "Java": 2, "SQL": 2, "Linux": 2, "CI/CD": 2,
        },
    },
    "ops": {
        "label": "运维 / SRE",
        "keywords": {
            "Linux": 3, "Docker": 2.5, "Kubernetes": 3, "CI/CD": 2.5,
            "Nginx": 2, "Prometheus": 2, "Terraform": 2, "AWS": 2,
            "阿里云": 2, "安全": 1.5, "Python": 2,
        },
    },
    "general": {
        "label": "通用岗位",
        "keywords": {
            "沟通协作": 2.5, "项目管理": 2, "数据分析能力": 1.5, "需求分析": 1.5,
            "团队管理": 1.5, "OKR": 1.5, "Python": 1.5, "SQL": 1.5,
            "办公软件": 1, "英语": 1,
        },
    },
}

ROLE_LABELS = {key: value["label"] for key, value in ROLE_PROFILES.items()}

# 院校层级（用于教育背景加分；不追求完备，可在 config 中调整加分值）
TOP_SCHOOLS = [
    "清华大学", "北京大学", "复旦大学", "上海交通大学", "浙江大学",
    "中国科学技术大学", "南京大学", "哈尔滨工业大学", "西安交通大学",
    "中国人民大学", "香港大学", "香港中文大学", "香港科技大学",
    "麻省理工学院", "斯坦福大学", "哈佛大学", "牛津大学", "剑桥大学",
    "加州大学伯克利分校", "卡内基梅隆大学", "新加坡国立大学", "东京大学",
]

TIER1_SCHOOLS = [
    "北京航空航天大学", "北京理工大学", "北京师范大学", "南开大学", "天津大学",
    "同济大学", "东南大学", "华中科技大学", "武汉大学", "中山大学", "华南理工大学",
    "四川大学", "电子科技大学", "重庆大学", "中南大学", "湖南大学", "山东大学",
    "厦门大学", "大连理工大学", "吉林大学", "东北大学", "西北工业大学", "兰州大学",
    "中国农业大学", "中央民族大学", "华东师范大学", "中国海洋大学", "西北农林科技大学",
    "国防科技大学", "北京邮电大学", "北京交通大学", "北京科技大学", "中央财经大学",
    "上海财经大学", "对外经济贸易大学", "中国政法大学", "华北电力大学", "西安电子科技大学",
    "南京航空航天大学", "南京理工大学", "苏州大学", "暨南大学", "深圳大学", "上海大学",
]

DEGREE_ORDER = ["博士", "硕士", "研究生", "本科", "学士", "大专", "专科", "中专", "高中"]

DEGREE_NORMALIZE = {
    "博士": "博士",
    "硕士": "硕士",
    "研究生": "硕士",
    "本科": "本科",
    "学士": "本科",
    "大专": "大专",
    "专科": "大专",
    "中专": "中专/高中",
    "高中": "中专/高中",
}

# 学历基准分：差距拉大，确保"中专 vs 名校本科"在同等经验下能拉开档位
DEGREE_SCORES = {"博士": 100, "硕士": 86, "本科": 70, "大专": 44, "中专/高中": 24, "其他": 32}

# 项目经历中的强动作动词（体现主导性与复杂度）
PROJECT_VERBS = [
    "主导", "负责", "设计", "搭建", "重构", "优化", "落地", "上线", "从0到1",
    "从 0 到 1", "带队", "推动", "迁移", "治理", "提效", "降本", "架构", "攻坚",
    "沉淀", "复用", "监控", "告警", "自动化", "拆分", "中台", "容器化",
]

# 英文停用词（JD 关键词提取时过滤）
STOPWORDS = {
    "the", "and", "for", "with", "you", "our", "are", "will", "able", "have",
    "has", "that", "this", "from", "your", "who", "what", "all", "can", "not",
    "job", "work", "team", "role", "years", "experience", "ability", "skills",
    "good", "strong", "plus", "etc", "using", "use", "new", "well", "more",
    "than", "into", "other", "about", "also", "such", "their", "them", "they",
    "need", "required", "requirements", "responsibilities", "preferred", "must",
    "and/or", "including", "related", "based", "within", "across", "using",
    "熟悉", "熟练", "掌握", "了解", "具备", "优先", "负责", "参与", "要求",
    "岗位", "职责", "任职", "以上", "相关", "工作", "经验", "能力", "能够",
}
