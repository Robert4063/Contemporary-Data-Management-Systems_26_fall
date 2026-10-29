# 第二阶段API说明

业务接口使用Authorization: Bearer令牌；完整请求模型可在运行服务的/docs查看。通用题正文为content中的五个字段；题目引用统一使用{question_id,question_version}，HTTP同时兼容旧version_id/source_version_id等别名，数据库仅存新结构。

| 方法与路径 | 用途及核心输入 |
| --- | --- |
| POST /api/auth/register、login；GET其/me；POST其/logout | username/password；令牌登录、当前账号、退出 |
| GET /api/questions/{question_id}/versions | 当前账号有权使用的完整版本内容 |
| GET /api/usual-questions | 仅当前用户已显式收录的版本；失效授权返回占位 |
| DELETE /api/usual-questions/{question_id}/{question_version}；DELETE其/{question_id} | 删除单版或该题全部收录，仅影响当前用户，返回removed_count |
| POST /api/surveys/from-questions | {items:[题目引用]}；创建独立问卷草稿 |
| PATCH /api/surveys/{id}/draft-content | 原问卷字段、可空deadline、revision；保留当前草稿 |
| PUT /api/surveys/{id}/questions/{local_id}/version | 题目引用、revision、clear_rules；仅切换本题 |
| GET/POST /api/surveys；POST其/drafts | 自己列表、创建问卷、空草稿 |
| GET/PUT /api/surveys/{id}；POST其/questions | 详情、带revision保存、增题 |
| POST /api/surveys/{id}/publish、publish-direct、close | 发布、保存并发布、关闭 |
| PATCH /api/surveys/{id}/metadata、deadline | 当前版本标题说明、截止管理 |
| GET/POST /api/surveys/{id}/versions | 版本历史；任一版本生成下一草稿；正文可为{revision,local_id?,selected_question?}，或指定题目版本同时换题 |
| GET /api/public/surveys/{id}及/status；POST其/next、responses | 填写、只读状态、答案检查、UUID提交 |
| GET /api/surveys/{id}/statistics及/{local_id} | 当前问卷版本的整卷、单题统计 |
| GET/POST /api/questions；GET其/{version_id} | 已授权/旧待确认发现列表、保存题目历史（不收录）、详情 |
| GET /api/questions/by-key/{question_id}/{question_version}；POST /api/usual-questions | 双字段查题；当前用户主动收录自己或已获授权的版本 |
| POST /api/questions/{version_id}/versions、restore；GET其/history | 修改新版本、恢复、历史 |
| GET/POST /api/questions/{version_id}/shares；DELETE其/{username} | 查授权、按用户名共享、撤销该版本授权 |
| POST /api/questions/forward | {username,items:[题目引用]}，单题或多题转发 |
| POST /api/surveys/{id}/reuse-question | 题目引用、local_id、revision；选入草稿 |
| GET /api/questions/{version_id}/usages、statistics | 引用查询、自己所有问卷的单题统计 |

创建通常201，修改/查询200，题库移除200；提交首次201、同次重试200。401会话无效，403接收者转发，404不存在或无权，409修订/状态冲突，410不可新填，422输入非法，503数据库不可用。

