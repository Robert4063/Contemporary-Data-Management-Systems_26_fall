# 第一阶段最终API

本文是调用协议，说明请求字段、响应语义、权限和错误处理；[项目完成报告](../04_项目完成报告/2026_ECNU_PJ1_1_杨家辉.pdf)对接口承担的功能作概述，不替代这些协议细节。实现见[main.py](../01_代码/backend/app/main.py)，请求模型见[models.py](../01_代码/backend/app/models.py)。

基础路径/api，请求响应为JSON。除注册、登录、健康检查外，API均要求Authorization: Bearer <access_token>。问卷ID为24位ObjectId字符串，非法ID统一404。网页HTML骨架不包含业务数据，状态和只读详情也必须登录。

## 端点

| 方法 | 相对/api的路径 | 输入/用途 | 成功 |
| --- | --- | --- | --- |
| GET | /health | 检查真实数据库连接 | 200 |
| POST | /auth/register | username,password注册 | 201 |
| POST | /auth/login | username,password获取会话 | 200 |
| GET | /auth/me | 验证当前会话 | 200 |
| POST | /auth/logout | 撤销当前会话 | 204 |
| POST | /surveys/drafts | 无请求体，创建按钮立即记录创建时间 | 201 |
| POST | /surveys | SurveyInput，正式创建草稿 | 201 |
| GET | /surveys?skip=0&limit=50 | 仅自己的问卷，limit为1–100 | 200 |
| GET | /surveys/{id} | 所有者获取完整配置 | 200 |
| PUT | /surveys/{id} | SurveyUpdate整体替换草稿 | 200 |
| POST | /surveys/{id}/questions | Question，末尾增题 | 201 |
| PATCH | /surveys/{id}/metadata | title,description,revision；题目不变 | 200 |
| PATCH | /surveys/{id}/deadline | deadline，已关闭不能修改 | 200 |
| POST | /surveys/{id}/publish | 无请求体，重验已存配置后发布 | 200 |
| POST | /surveys/{id}/publish-direct | SurveyUpdate，原子保存当前内容并发布 | 200 |
| POST | /surveys/{id}/close | 无请求体，手动关闭为终态 | 200 |
| GET | /public/surveys/{id}/status | 状态与全部只读题目详情 | 200 |
| GET | /public/surveys/{id} | 可填写问卷完整配置 | 200 |
| POST | /public/surveys/{id}/next | Submission，重放答案前缀 | 200 |
| POST | /public/surveys/{id}/responses | ResponseSubmission，最终校验保存或重试确认 | 201/200 |
| GET | /surveys/{id}/statistics | 所有者整卷统计 | 200 |
| GET | /surveys/{id}/statistics/{qid} | 所有者单题统计 | 200 |

完整机器接口定义由运行中的服务在/openapi.json提供；集合定义见[MongoDBSchema.json](../01_代码/MongoDBSchema.json)。

## 账号与会话

注册/登录请求为{"username":"alice","password":"example-password-123"}。用户名3–32位ASCII字母数字下划线且规范为小写，密码8–128位。登录返回access_token、token_type=bearer和expires_at，默认24小时。me返回id、username。前端复用同源共享令牌但始终先验证me，失效才重新登录；接口仍以服务器会话为准，不信任本地登录标记。退出清理本地并同步标签页。

## 问卷配置与发布

正式创建请求示例（需将截止改为实际未来时间）：

```json
{
  "title":"课程反馈","description":"请按实际情况回答",
  "allow_anonymous":true,"allow_multiple":false,
  "deadline":"2030-12-31T23:59:00+08:00",
  "questions":[
    {"id":"q1","title":"参加过课程吗","type":"single","required":true,
     "options":[{"id":"yes","label":"是"},{"id":"no","label":"否"}],
     "jump_enabled":true,"rules":[{"op":"eq","value":"no","target":"q3"}]},
    {"id":"q2","title":"喜欢哪些内容","type":"multiple","required":true,
     "options":[{"id":"a","label":"设计"},{"id":"b","label":"实现"}],
     "validation":{"min_choices":1,"max_choices":2},"jump_enabled":false},
    {"id":"q3","title":"年龄","type":"number","required":true,
     "validation":{"minimum":0,"maximum":120,"integer_only":true},
     "jump_enabled":true,"rules":[{"op":"lt","value":18,"target":"END"}]},
    {"id":"q4","title":"建议","type":"text","required":false,
     "validation":{"min_length":2,"max_length":300},"jump_enabled":false}
  ]
}
```

POST返回含_id、revision=1、status=draft的完整文档。PUT与直接发布使用上述字段并加revision；不接收owner_id、created_at、status等服务器字段。增题用相同Question结构，指向的后续题须已经存在；可先增题再PUT完整规则。

创建按钮专用drafts允许初始deadline=null和空题目，立即写created_at；其他正式创建/整体保存、截止调整与发布，截止必填、含秒及时区的ISO8601，拒绝null、空字符串、数字时间戳和无效日期。不早于创建且设置时在未来，统一UTC毫秒。发布还要求至少1题。

普通发布重验数据库配置，返回{"status":"published","share_url":"/survey/<id>"}。直接发布用当前完整配置一次原子写入内容、published_at和状态，revision递增，返回status、revision、share_url。取消确认是前端不发请求；后端仍检查所有者、draft、revision和全部配置，失败不部分写入。

发布后仅metadata可改问卷标题/说明；截止可调整但closed不能改。题目、规则、匿名及次数策略不接受发布后变更。所有管理与统计均限制所有者。

## 跳转配置

jump_enabled严格布尔；新客户端应明确传false或true。关闭保留合法rules但不执行；缺字段旧格式按rules非空推断开启。单选每选项最多1个eq目标；多选/数字最多1条，文本不能开启。单选eq/ne旧格式仍兼容，旧发布多规则按首条命中读取，不等于允许新多选/数字配置多规则。

多选op为contains_any、contains_all、contains_none，value为非空、无重复、本题选项ID数组。contains_none仅已作答且无交集时触发。数字eq/ne/gt/gte/lt/lte与between闭区间检查范围和类型。target必须是后续题ID或END。选项字母及题目显示标题不是规则ID；重命名和重新编号不改rules.value/target。

## 状态与只读详情

status返回_id、title、description、status、deadline、created_at、questions、availability、can_fill、message、server_time。题目白名单仅id/title/type/required/options/validation，不含规则、他人答卷、身份、统计或admissions。

availability为open、expired、closed、incomplete，只有open允许填写。已截止或关闭返回200供只读展示，closed优先于时间，截止等于当前时间即expired。不存在、未发布草稿或非法ID404；未登录401。普通填写配置、next与新提交不可填时仍410；只读访问不是提交准入。

## 按题填写与幂等提交

Submission为{"answers":{},"anonymous":true}，next重放前缀并返回path、规范answers、next_question_id、complete、errors。例如q1=no跳q3，q3=16时结束，q2与q4不需要回答。单选发选项ID，多选发ID数组，数字发JSON number；字符串数字和true不是数字答案。

可选题在next用null明确跳过；最终提交可省略可选题。空答案不保存。夹带跳过题、未知题或非法必答/类型/范围返回422具体题目信息。

最终请求必须有UUID：

```json
{"submission_id":"10cc0832-e43f-49e0-bdb5-d45e3c18a72a","answers":{"q1":"no","q3":16},"anonymous":true}
```

上例UUID应使用客户端实际生成的合法UUID；首次成功201返回id、message=提交成功、replayed=false。同次同内容重试200返回相同id及replayed=true；同次换内容409。明确重新填写用新UUID，仍服从allow_multiple，默认每账号一次。成功后即使关闭仍可确认原结果，新提交不再允许。旧客户端不带UUID会422。

匿名仍需登录，且问卷必须允许；不保存原始账号署名。换匿名模式不绕过每账号一次。

## 统计与错误

整卷返回survey_id、response_count、questions；单题返回question_id、title、type、answer_count，选择题另有options（id/label/count），文本与数字另有contents，数字另有average。保留零票选项，无回答的数字average=null；按已保存答卷人次统计，题目字母由前端按当前options顺序显示。

| 状态 | 含义 |
| --- | --- |
| 401 | 未登录、令牌无效或过期 |
| 404 | 无效ID、不存在、未发布或无管理权限 |
| 409 | 版本/状态冲突、超次数或同次内容变化 |
| 410 | 当前不能新填/提交，状态与只读详情端点仍可200 |
| 422 | 字段、题目配置、截止或答案非法 |
| 503 | 数据库暂时不可用 |

模型错误detail数组仅含loc/msg/type；不回显密码。答案错误detail.errors含question_id/message。数据库细节只写服务端日志。响应与可选字段以导出的OpenAPI为准。

## 调用顺序与客户端处理

| 场景 | 调用顺序 | 调用方应如何处理 |
| --- | --- | --- |
| 打开分享链接 | GET /auth/me，成功后GET /public/surveys/{id}/status | 401要求登录；can_fill=false展示只读详情，200不代表可提交 |
| 开始填写 | GET /public/surveys/{id}，逐步POST /next | 每次发当前有效答案前缀；以后端返回的next_question_id推进 |
| 最终提交 | 生成并保存本次UUID，POST /responses | 201为新答卷，200且replayed=true为原成功；丢包重试沿用UUID与内容 |
| 修改草稿 | GET管理详情，PUT完整配置与当前revision | 409先重新获取，不能自动覆盖别人或其他标签的新版本 |
| 发布 | POST /publish或携带完整配置POST /publish-direct | 前端先确认；422修正配置，409重新加载，不能当成成功 |

以下为响应结构示例，用于说明协议，不作为新增的实测记录。对必答数字题q3提交非整数时，可以得到：

```json
{
  "detail": {
    "message": "答案校验失败",
    "errors": [{"question_id": "q3", "message": "必须输入整数"}]
  }
}
```

调用方应显示题目级错误并保留可修改的答案；遇410则重新读取status切换只读展示。不同错误的恢复方式不同，不能统一重置登录或反复自动发布。统计只允许所有者访问，不能因持分享链接就调用管理统计接口。
