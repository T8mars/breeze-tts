# T8star-Aix Voice Studio · Breeze TTS 2 整合项目路线图

更新时间：2026-09-01
上游代码基线：`breezeblue-ai/breeze-tts@ca632ce6c4d05f7985da4eab29b1a5d445b43f7b`
官方模型基线：`BreezeBlue/Breeze-TTS-2@c1c8ca18b70b30822735633991d9ebf4898e47d4`

## 目标

交付两个相互独立、可验证、可分发的发行物：

1. Windows 10/11 x64 的 Breeze TTS 2 便携整合包。界面布局和配色参考
   用户提供的 IndexTTS 2.5 整合包截图与设计上下文，但不复制 IndexTTS 品牌素材。
2. 名为 `comfyui-breeze-tts-T8` 的 ComfyUI 自定义节点包。

两个发行物都必须支持 Breeze TTS 2 的三种官方能力：Voice Design、Voice Clone、
Voice Direction，并保持中英文、声学事件、24 kHz 单声道输出、seed 与 CFG 语义。

## 不可妥协的约束

- 桌面包使用完全隔离的 Python 运行时，精确锁定官方兼容栈。
- ComfyUI 节点不得安装、升级或降级 ComfyUI 的 Torch、Torchaudio、Transformers、
  Tokenizers 或 NumPy。
- ComfyUI 节点必须兼容 Transformers 4.57.x 和当前 5.x；无法兼容的版本只给出
  可操作诊断，禁止静默执行 pip。
- 模型、衍生模型和自托管输出仅限研究与非商业用途。首次下载模型前必须显式接受
  完整模型协议，并保留 Apache 2.0、MODEL_LICENSE、NOTICE 和第三方许可证。
- 默认运行路径为 eager。未经 Windows 消费级显卡实测的 `fast-all` 只能作为实验选项。
- 程序、模型、用户数据、日志、输出与更新缓存相互隔离。
- 节点导入与桌面启动器打开时不得加载模型、联网或编译 CUDA 内核。
- 相同环境、相同输入和相同 seed 必须满足确定性容差；取消、OOM 和异常后下一次生成仍可用。

## 视觉规范

| 用途 | 值 |
| --- | --- |
| 主背景 | `#0f1117` |
| 卡片 | `rgba(25, 28, 38, 0.86)` |
| 输入背景 | `#10131a` |
| 主文本 | `#f4f6fb` |
| 次级文本 | `#b9c0cf` / `#858ea2` |
| 主强调色 | `#fb7299` |
| 主按钮 | `linear-gradient(135deg, #fb7299, #ff4d84)` |
| 辅助蓝 | `#aebeff` |
| 成功 / 警告 / 错误 | `#45d483` / `#f5a524` / `#ff5b6e` |

字体使用 Segoe UI、Microsoft YaHei UI；卡片圆角 18 px，输入和按钮圆角 10 px；
宽屏双栏、窄屏单栏；背景使用克制的粉色/蓝色径向柔光。

## 目标目录

```text
breeze-tts/
├─ breeze_infer/                 # 官方推理入口
├─ models/                       # 官方模型代码；权重置于 models/Breeze-TTS-2
├─ t8_runtime/                   # 桌面与节点共享的安全适配层
├─ desktop/                      # Electron 桌面启动器
├─ comfyui-breeze-tts-T8/        # 独立 ComfyUI 发行物
├─ packaging/                    # Python/runtime/portable 构建脚本
├─ manifests/                    # 模型和运行时固定清单
├─ tests_t8/                     # 整合层测试与验收测试
├─ dist/                         # 构建产物，不纳入 Git
└─ roadmap.md
```

## 桌面包范围

- Electron 安全壳：CSP、context isolation、禁用 renderer Node、外链白名单。
- 启动器：模型选择、完整性检查、协议确认、下载/续传/取消、GPU 和依赖诊断、
  启动/停止、日志、输出目录。
- 本地 WebUI：Voice Design、Voice Clone、Voice Direction 三种模式；参考音频播放器、
  准确逐字稿、演绎指令、声学事件示例、seed、CFG、流式播放、取消与下载。
- 模型下载固定 revision，执行磁盘预检、临时文件、大小/SHA-256 校验和原子完成标记。
- 输出为非空、有限值的 24 kHz 单声道 WAV；记录生成元数据。
- 应用退出时停止 Python 子进程并释放显存。
- 同时提供便携 ZIP 与明确标注的 7-Zip 自解压包；支持可选 Authenticode 签名及仅由环境变量启用的 HTTPS 更新源。

## ComfyUI 节点范围

菜单：`T8star-Aix/Audio/Breeze TTS`。内部 ID 使用 `T8_BreezeTTS_*`。

节点：

1. Breeze TTS Model Loader
2. Breeze Voice Design Request
3. Breeze Voice Clone Request
4. Breeze Voice Direction Request
5. Breeze Generation Settings
6. Breeze TTS Generate
7. T8 Desktop Voice Bundle
8. T8 Per-Line Direction

输出采用标准 ComfyUI `AUDIO`：`waveform=[1,1,T] float32`、`sample_rate=24000`，
并输出包含模式、seed、CFG、模型 revision、耗时、RTF、警告和缓存信息的 JSON。

兼容策略：节点不依赖 `qwen-tts==0.1.1` 的安装元数据；在自己的命名空间内保留经审计的
最小 Qwen 12 Hz audio tokenizer 推理代码和许可证。Transformers 4.57.x 使用上游兼容实现，
5.x 使用原生 T5Gemma2 与 API 适配。若同进程路径在某个受支持环境中确实不可行，才允许使用
预构建、显式启用的隔离 sidecar，且不得在节点执行时创建环境。

## 实施与验收阶段

### Phase 0 — 兼容性门禁

- [x] Windows 隔离环境安装官方精确依赖。
- [x] 官方 BF16 eager 在 Windows NVIDIA GPU 上真实生成。
- [x] Design、Clone、Direction 各完成中文和英文样例。
- [x] 确定桌面内置 Python 小版本与 CUDA runtime。
- [x] Transformers 4.57.x 和当前 5.x 的节点导入、模型加载与真实生成通过。
- [x] 节点安装前后 Torch/Torchaudio/Transformers 版本不变。

### Phase 1 — Desktop MVP

- [x] Electron 启动器和参考风格完成。
- [x] Python 本地服务、WebUI、流式生成和取消完成。
- [x] 模型清单、协议接受、固定 revision 下载、续传与校验完成。
- [x] GPU/依赖诊断、日志和模型生命周期完成。
- [x] 中文、空格和长路径测试通过。

### Phase 2 — ComfyUI MVP

- [x] 八个组合节点和标准 AUDIO 适配完成。
- [x] 4.57.x/5.x 双兼容层完成。
- [x] 模型缓存、参考编码缓存、RNG 隔离、串行锁和中断处理完成。
- [x] 示例工作流、Manager/Registry 元数据和结构化错误完成。
- [x] 当前 ComfyUI `/object_info`、Preview Audio 与 Save Audio 集成通过。

### Phase 3 — 分发与质量门禁

- [x] Apache 2.0、MODEL_LICENSE、NOTICE、第三方声明和非官方说明齐全。
- [x] Python runtime、依赖锁和 Electron 产物可复现。
- [x] 干净 Windows 机器不安装 Python/Git/CUDA Toolkit 也可启动。
- [x] 连续 20 次生成显存稳定；取消和一次 OOM 后可继续。
- [x] 诊断报告不包含用户文本、音频、密钥或未授权隐私路径。
- [x] 构建便携 ZIP，生成 SHA-256 清单并执行解包冒烟测试。

### 0.1.2 扩展能力

- [x] Tokenizer 感知的长文本拆分、顺序批量及合并 WAV。
- [x] 多角色脚本、角色音色映射与可复用音色库。
- [x] SRT 解析、时间轴合并及可选 faster-whisper 转写。
- [x] 24GB 级显卡 Fast All 安全门与实验配置。
- [x] 大包自解压 EXE、可选 Authenticode 签名、离线 manifest 校验和可配置 HTTPS 自动更新。

量化或低显存衍生模型仅在许可证、NOTICE 和质量回归均通过后考虑。

## 完成定义

只有在以下证据同时存在时才能宣称完成：

- Windows 真实模型推理报告；
- ComfyUI 4.57.x 与 5.x 的测试报告；
- 八个节点的示例工作流和可发现性证据；
- 模型下载/校验清单；
- 便携包构建产物、SHA-256 和解包冒烟测试；
- 许可证与第三方声明审计；
- 逐项对应本路线图的完成审计。
