# 基于MongoDB的问卷系统：第二阶段提交材料

## 1. 材料结构

| 目录 | 内容 |
| --- | --- |
| 01_代码 | 后端、前端、MongoDBSchema.json、配置与启动脚本 |
| 02_文档 | 系统说明、数据库设计、API说明、关键逻辑说明 |
| 03_测试用例 | 自动测试、测试步骤与结果、一份实际结果JSON |
| 04_项目完成报告 | 项目完成报告PDF、AI使用日志 |

## 2. 安装与启动

准备Python 3.11+、MongoDB和浏览器。在本目录打开PowerShell：

```powershell
Set-Location './01_代码'
python -m venv .venv
& './.venv/Scripts/python.exe' -m pip install -r backend/requirements.txt
& './.venv/Scripts/python.exe' scripts/manage.py init
```

配置位于01_代码/.env：MONGO_URI是连接地址，MONGO_DB是业务库名，APP_SECRET用于匿名去重，SESSION_HOURS是登录时长。接续第一阶段数据时沿用原连接、库名和APP_SECRET。

MongoDB已运行时直接启动应用；手动启动时，在01_代码的另一个终端执行：

```powershell
$env:MONGOD_BIN='D:\MongoDB\server\8.3\bin\mongod.exe'
& './.venv/Scripts/python.exe' scripts/manage.py mongo
```

在01_代码目录启动前后端：

```powershell
& './.venv/Scripts/python.exe' scripts/manage.py serve
```

打开[应用](http://127.0.0.1:8000/)，[接口文档](http://127.0.0.1:8000/docs)提供完整请求模型。

## 3. 第二阶段操作

1. 编辑问卷时点击“放入题库”，或在题库点击“保存并放入题库”；保存、修改、恢复和转发不自动收录。
2. 在题目⋯菜单选择单题转发或多选，输入用户名。接收者可选用共享题，加入自己的题库需主动收录。
3. 同源已收录版本归组展示，支持删除单版或整题收录。
4. “切换版本”逐版展示完整内容；“基于此题创建下一版”先保存当前草稿，再保留全部题目创建下一问卷版本，两版草稿可分别修改。
5. 题目详情提供历史、引用问卷和跨卷统计，统计范围为当前用户自己的问卷。

## 4. 测试

完整测试需要Chrome。在01_代码执行：

```powershell
& './.venv/Scripts/python.exe' -m pip install -r '../03_测试用例/requirements-test.txt'
& './.venv/Scripts/python.exe' scripts/manage.py test
```

也可进入03_测试用例，设置SURVEY_TEST_SCOPE=full后用该解释器执行python -m pytest -v。MONGOD_BIN指定mongod路径，TEST_MONGO_URI可指定测试服务，PLAYWRIGHT_CHROMIUM_EXECUTABLE可指定Chrome路径；测试使用随机隔离库。

步骤、输入、输出和结果见[测试用例与结果](03_测试用例/测试用例与结果.md)，全量记录见[实际结果](03_测试用例/实际结果.json)。设计从[系统说明](02_文档/系统说明.md)开始阅读。
