# 视觉资产清单（visual asset inventory）

本仓库的对外文档、Skill 适配说明、PR 截图都引用 `docs/assets/` 下的文件。
条目按"已存在 / 计划中 / 验证流程"分组；只有"已存在"行能外发，未标注"已存在"的是 Roadmap，不在任何对外材料里张贴合成图。

## 已存在（保证与代码一致）

| 文件 | 内容 | 来源 | 用在 |
|---|---|---|---|
| `docs/assets/claim-studio-home.png` | 工作台首页截图 | 真实 `webapp.py` `GET /` 渲染（本地） | `README.md`、`docs/PROOF_GUARD_SKILL.md` |
| `docs/assets/template-media-preview.png` | 三段分镜合成示意图 | `media.py` `build_template_storyboard` 实际产物 (720×1280) | `docs/SERVICE.md` |
| `docs/assets/template-media-demo.mp4` | FFmpeg 生成的 15s 演示视频 | 真实 `render_video` 产出 | 外部 resume 链接 |
| `docs/assets/approval-workflow-timeline.svg` | 多人审批状态机 + 审核时间轴 | 当前源码绘制的 SVG | `README.md`、`docs/SERVICE.md` |

## 计划中（进 Roadmap 时同时标注此章节）

| 主题 | 触发条件 | 不允许的样态 |
|---|---|---|
| `<webapp>` 截图（5 策略切换下拉） | 待 `webapp.py` 加入策略切换控件后由真实运行截图 | 不允许用 mock UI 合成 |
| `<webapp>` 截图（Webhook 推送测试响应） | 待 Webhook 推送（v0.6.0 路线）实施后由真实日志生成 | 不允许贴无关服务返回 |
| 多模态合成 demo（短视频 → 字幕 → 草图） | 当前 `media.py` 已有实现但 FFmpeg 安装前置依赖用户本地环境 | 不允许贴非本仓库运行的素材 |
| Hugging Face Spaces 静态 demo 截图 | 待 `sites` skill 部署完成（v0.6.0 路线） | 不允许贴陈旧 UI |

## 校验

每张图添加时必须满足：

1. **可在仓库根目录反复生成**——附 build 命令（例如 `make assets` 或 Python 脚本），README 给出的链接来自这个命令的最近一次产出；
2. **数字与 `docs/STATE.md` 一致**——不重新合成与当前代码不符的统计图；
3. **去 AI 味**——文字图层只描述图中真实结构，不出现营销措辞；
4. **CC-BY-4.0 之外的内容不引入**——任何外部素材必须在 PR 描述里附原始来源链接。

## 工作约定

新增视觉资产 PR 标题必须带 `[assets]` 前缀，并对应 `docs/SERVICE.md` 的"演进方向"段有一起加上的说明；不接受纯贴图。
