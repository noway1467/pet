# 塔菲 Live2D 制作规格

## 形象基准

- 以 `references/taffy_reference_expressions.png` 和 `references/taffy_reference_actions.png` 为唯一造型基准。
- `taffy_master.png` 是去背景后的正面分层母版，后续切层、补全遮挡区域和 Cubism 建模都围绕该图进行。
- 保留粉色长发、黄铜护目镜、右侧小挂件、齿轮发夹、金色眼睛、海军蓝/酒红学院服、青色宝石和棕色短靴。

## 必需参数

- `ParamAngleX`、`ParamAngleY`、`ParamAngleZ`：头部随鼠标转动。
- `ParamEyeBallX`、`ParamEyeBallY`：眼球随鼠标移动。
- `ParamEyeLOpen`、`ParamEyeROpen`：自动眨眼和闭眼表情。
- `ParamMouthOpenY`、`ParamMouthForm`：说话、微笑、哭泣和惊讶。
- `ParamBodyAngleX`、`ParamBodyAngleY`、`ParamBodyAngleZ`：身体跟随与动作摆动。
- `ParamBreath`：待机呼吸。

## 必需表情

- 默认微笑、闭眼开心、害羞、嚎啕大哭、眯眼露齿笑、汗滴尴尬、委屈哭、金钱眼、好耶、惊讶。
- 已提供可直接从动作菜单触发的脸部 motion：`face_happy`、`face_shy`、`face_surprised`、`face_nervous`、`face_sad`、`face_smug`、`face_angry`、`face_wink`。
- 所有脸部 motion 结束时都会平滑回到中性参数，避免眼睛、眉毛或嘴形卡在表情状态。

## 必需动作

- 待机呼吸、眨眼、头发与挂件物理摆动。
- 开心抬腿舞步、闭眼陶醉摇摆、好奇歪头、被抓起后四肢自然下垂。
- 兼容项目的点击、摸头、拖拽、落下和随机动作入口。

## 导出目标

- `taffy.moc3`
- `taffy.model3.json`
- `taffy.physics3.json`
- `taffy.cdi3.json`
- `textures/texture_00.png`
- `expressions/*.exp3.json`
- `motions/*.motion3.json`
