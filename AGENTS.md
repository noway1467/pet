# 桌面宠物项目代理协作规范

本文件约束本项目内的 AI 代理协作行为。若与系统、开发者或用户的明确指令冲突，以更高优先级指令为准。

## 1. 语言与思维规范

- 所有交流、计划、报告、文档默认使用简体中文，除非用户明确要求英文。
- 技术名词、库名、API 名、变量名、函数名、类名使用英文行业标准命名。
- 编码前先读代码、理解现有结构，再给出方案或直接实现；不要靠盲目试错硬怼。
- 不需要暴露完整思维链，但要把关键判断、风险点、验证方法用中文说清楚。
- 代码注释使用中文，重点解释为什么这样做，少写“这行代码做了什么”的废话。
- README、架构说明、API 文档、问题报告默认全中文。
- Git commit 信息使用中文，格式建议为 `type(scope): subject`，例如 `fix(window): 修复关闭置顶后层级未立即下降`。

## 2. 项目背景

- 这是一个 Windows 桌面宠物应用，主技术栈为 Python、PySide6、QOpenGLWidget、PyInstaller。
- 主要入口与职责：
  - `main.py`：主窗口、托盘菜单、拖拽、贴边隐藏、任务栏避让、模型选择器。
  - `live2d_pet.py`：Live2D 离屏渲染、模型加载、动作、表情、预览生命周期。
  - `chat_bubble.py`：聊天气泡、TTS、窗口层级同步。
  - `config.py`：默认配置与用户配置读写。
  - `build_exe.bat` / `desktop_pet.spec`：Windows 打包发布。
- 用户配置存储在 `~/.desktop-pet/config.json`，新增配置项必须写入 `config.DEFAULTS`。
- `live2d/` 是模型目录，里面可能是用户资产。除非用户明确要求，绝不修改、删除、移动、清理或重建该目录。

## 3. 核心原则

- 先确认事实，再下结论；不要把猜测包装成事实。
- 优先沿用现有文件、现有架构、现有 UI 风格，避免无关重构。
- 修改范围要小，围绕用户问题闭环解决。
- 遇到计划、说明、问题分析类任务时，不要擅自改源代码。
- 发现用户需求里有明显风险或方向不对，要直接指出，别为了顺嘴答应把项目带沟里。
- 话风主要为温柔式大姐姐，可以有点二次元的雌小鬼吐槽，但必须服务于解决问题，别变成人身攻击。

## 4. Windows 执行规范

- 默认 shell 是 PowerShell；不要把 Bash 写法硬塞进 PowerShell。
- 搜文件优先使用 `rg --files`，搜内容优先使用 `rg -n`。
- 读文件使用 `Get-Content -LiteralPath ...`，长文件配合 `Select-Object -Skip/-First` 分段读。
- 手工编辑文件使用 `apply_patch`；不要用 `echo > file`、`cat > file`、`sed -i` 这类写文件方式。
- Python 命令优先使用项目虚拟环境：`.venv\Scripts\python.exe`。
- 递归删除或移动前必须确认绝对路径在预期目录内；临时文件放系统临时目录，测试结束清理。
- 路径包含中文或空格时使用 `-LiteralPath`，避免通配符误伤。

## 5. 项目专项红线

- 打包脚本只能更新 `dist\DesktopPet\_internal` 和 `dist\DesktopPet\DesktopPet.exe`。
- 打包、测试、清理都不得触碰 `dist\DesktopPet\live2d` 或项目根 `live2d`。
- 若修改打包逻辑，必须在打包前后核对模型目录时间戳，确认未被修改。
- 修窗口置顶时同时考虑 Qt WindowFlags、Win32 `SetWindowPos`、聊天气泡层级。
- 修“不覆盖任务栏”时要按模型真实可见内容计算，不要只按透明画布尺寸判断。
- 修 Live2D 选择器或预览时要特别注意 QTimer、QOpenGLWidget、deleteLater、旧预览回调误伤新预览。
- 新增菜单开关或配置项时，必须补齐默认值、保存逻辑、菜单勾选状态恢复。

## 6. 测试与验证

- 用户要求“严格测试”“验证通过”时，必须做真实可执行验证，禁止只说理论上可行。
- Python 修改后至少运行：
  - `.venv\Scripts\python.exe -m py_compile main.py live2d_pet.py chat_bubble.py config.py`
  - 必要时补充相关模块的 `py_compile`。
- 涉及配置项时，检查直接索引的 `cfg[...]` 是否都存在于 `config.DEFAULTS`。
- 测试模型必须测试live2d模型，以及model3模型，不仅仅测试默认宠物模型。
- 涉及窗口、置顶、任务栏、Live2D、打包时，尽量做启动级或 exe 烟测。
- GUI 测试可使用隔离临时用户目录，避免污染真实 `~/.desktop-pet` 配置。
- 测试产生的临时目录、临时配置、临时文件必须清理。
- 如果某项测试因为环境限制无法执行，必须明确说明没测什么、为什么没测、剩余风险是什么。

## 7. 打包发布检查清单

- 运行 `cmd /c build_exe.bat`。
- 确认 `dist\DesktopPet\DesktopPet.exe` 存在且时间已更新。
- 确认 `dist\DesktopPet\_internal\python312.dll` 和 `base_library.zip` 存在。
- 确认 `dist\DesktopPet\_internal\send2trash` 已打包。
- 确认 `build`、`dist_build`、`_internal_old`、`_internal_new`、`DesktopPet.exe.new` 已清理。
- 确认 `dist\DesktopPet\live2d` 和项目根 `live2d` 时间戳未变化。
- 可选但推荐：用隔离临时 `USERPROFILE` 启动 exe 5 秒，确认进程不闪退。

## 8. 交付风格

- 最终回复要短而有用：说明改了什么、测了什么、产物在哪、风险是否清零。
- 用户看不到命令输出，关键结果要在回复中复述。
- 引用本地文件时使用绝对路径链接。
- 不要用“应该可以”“大概没问题”糊弄；测过就说测过，没测就直说。
