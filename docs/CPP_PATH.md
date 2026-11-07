# C++ 编译路径（v1.5 起已迁移为独立产品线）

> 由 Luo-Jacob 制作 · MIT License

## 迁移说明

v1.4 时期这里放的是「参考骨架 + 编译路径」文档，`clicker.cpp` 只是给用户自己改
`user_code()` 的示例，**未实测编译**。

**v1.5 起 C++ 正式成为第二条独立产品线**，源码、构建脚本、文档全部迁到
[`cpp/`](../cpp/README.md)：

| 旧 | 新 |
| --- | --- |
| `docs/clicker.cpp`（未编译验证的参考骨架） | `cpp/runtime_template.cpp`（由 `cpp/studio.cpp` 导出，已实测编译） |
| 用户改代码后自己敲 g++ 命令 | `cpp/AutoClickerCPP.exe` 工作台：编辑器 + 初始模板 + **一键真编译** |
| 文档里的编译命令 | `cpp/build_cpp.bat` 一键构建 |
| 无版本、无 Release | 独立版本 `cpp-v1.0.x`、独立 Release |

旧的 `docs/clicker.cpp` 已移除，避免同一套运行时存在两份、升级时互相漂移。

**关键变化**：v1.5 的 C++ 线写的是**真 C++ 代码**，由本机 `g++` / `clang++` 真正编译，
不是 v1.4 Python 版里那套自写的类 C 伪代码 DSL 解释器。两条线的「高级」互不依赖。

## 编译命令（摘要）

```bat
cd cpp
build_cpp.bat
```

或手动：

```bat
g++ -O2 -s -static -mwindows -std=c++11 cpp/studio.cpp -luser32 -lcomdlg32 -lshell32 -o AutoClickerCPP.exe
python cpp/export_template.py
g++ -O2 -s -static -mwindows -std=c++11 cpp/runtime_template.cpp -luser32 -o out\AutoClickerScript.exe
```

MSVC：`cl /O2 /MT /EHsc /DUNICODE /D_UNICODE cpp/studio.cpp /link user32.lib comdlg32.lib shell32.lib`

`-static` 静态链接 libgcc/libstdc++，`-s` 去符号，`-mwindows` 不弹控制台。

## 实测体积与依赖（v1.5，本机 MinGW64 g++ 4.9.2）

| 产物 | 体积 | 控制台 | DLL 依赖 |
| --- | --- | --- | --- |
| `AutoClickerCPP.exe`（工作台） | 573 KB | 无 | 仅系统 DLL（KERNEL32 / USER32 / GDI32 / SHELL32 / comdlg32 / msvcrt） |
| `AutoClickerScript.exe`（用户脚本产物） | **42 KB** | 无 | 仅 KERNEL32 / USER32 / msvcrt |
| `AutoClicker.exe`（Python onefile） | 约 31 MB | 无 | 已打包 |

手写稿的「≤5 MB / 精简核心 ≤2 MB」目标，C++ 线已达成；Python 线保留完整 GUI、托盘、
主题、背景图与节点循环，体积换功能。两者互补。

编译器只在**编译时需要**，产物拷到任意 Windows 直接运行，无需 MinGW / VC 运行库。

## 技术实现：基础版 vs 高级版

### 基础版（两条线都已实现）

`SendInput` + `MOUSEINPUT` / `KEYBDINPUT`，与真人操作走同一条系统输入链路；
`GetCursorPos` / `SetCursorPos` 实现坐标拾取与固定坐标点击（点击后恢复光标）；
`RegisterHotKey` 全局热键；可中断等待让急停立即生效。

### 高级版：数据包级点击模拟（**仅规划，不实现**）

| 路径 | 做法 | 代价 / 风险 |
| --- | --- | --- |
| 过滤驱动 | 自写 HID/键盘类过滤驱动，向设备栈注入报告 | 驱动签名、管理员权限、蓝屏风险、维护成本极高 |
| 虚拟 HID 设备 | 用 UMDF / ViGEm 创建虚拟设备 | 需要安装驱动，与「零额外依赖」冲突 |
| 协议层 | 针对 adb / 远程桌面 / 网络输入协议直接发包 | 只对特定目标有效，非通用 Windows 点击 |
| 内核回调注入 | 调用未文档化的输入栈内部函数 | 版本强相关、稳定性差、易被安全软件拦截 |

不实现的原因：与「单文件、无额外依赖、免安装」目标冲突；需要内核签名与管理员权限；
这类能力常被用于绕过反作弊 / 自动化风控，分发合规与误用风险高；对绝大多数场景
`SendInput` 已是系统级物理输入，效果等价且稳定。

**只在此记录技术路线与取舍，不提供代码 / 驱动 / 绕过机制。**

## 已知限制

- 仅支持 Windows（`SendInput` / `RegisterHotKey`）。
- 以管理员权限运行的程序可能收不到普通权限进程的注入输入，请让两者权限级别一致。
- 固定坐标点击会短暂移动物理光标；多个动作并行且各设不同坐标时会争用同一个系统光标，
  运行时用「点击后立即恢复」缓解，但无法完全消除。
- 无修饰键的单键热键会全局独占该按键，建议使用组合键。
- 编译需要本机已有 C++ 编译器；工作台会自动探测常见安装路径，也可手动指定。

---

由 **Luo-Jacob** 制作 · <https://github.com/Luo-Jacob/autoclicker> · MIT License
