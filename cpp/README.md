# AutoClicker C++ Studio

> **独立产品线** · 版本 `cpp-v1.0.0` · 与 Python 版 (`src/`) 各自独立版本与 Release
> 由 Luo-Jacob 制作 · MIT License

## 这是什么

一个**单文件、零额外依赖**的 Win32 GUI「C++ 代码工作台」：

- 编辑器里写的是 **真 C++ 代码**（不是伪代码、不是自写 DSL 解释器）；
- 打开就带一份**初始模板**，内置 `rand / left / right / middle / keyboard / pause / hotkey / mouse / point`，
  用户代码区**不需要写任何 `#include`**；
- 点「编译」会把「内置运行时 + 你的代码 + 主循环」拼成完整 `.cpp`，调用本机 `g++` / `clang++`
  **真正编译**成另一个独立 exe；
- 编译产物：无控制台、无 DLL、无 Python，只链接系统 `user32`，约 **42 KB**。

编译器只在**编译时需要**；编译出来的 exe 拷到任何 Windows 上直接跑，不需要装 MinGW / VC 运行库。

## 快速开始

```
双击 AutoClickerCPP.exe
  → 编辑器里改 user_code()（初始模板可直接编译运行）
  → 点「编译并运行」或「编译」
  → 按 Ctrl+Alt+X 开始 / 再按一次停止
  → Ctrl+Alt+F12 随时急停并退出脚本进程
```

工作台会自动探测编译器（PATH 里的 `g++`、Dev-C++ / MSYS2 / TDM-GCC / LLVM 的常见安装路径）。
没找到时点「编译器…」手动选 `g++.exe` 或 `clang++.exe`，设置会记在 `compiler.ini`。

| 按钮 | 作用 |
| --- | --- |
| 新建 / 打开 / 保存 | 载入初始模板、打开或保存 `.cpp` 代码 |
| 编译并运行 / 编译 / 运行 / 停止运行 | 真编译、启动产物、终止产物进程 |
| 完整源码 | 导出**真正交给 g++ 的合并后 .cpp** 并用记事本打开 |
| 编译器… / 清空日志 / 帮助 / 关于 | 指定编译器、清日志、函数速查、版本信息 |

编译输出默认写到 `out/AutoClickerScript.exe`；编译命令与 g++ 的完整报错都显示在下方日志区。

## 内置函数（真 C++ 函数）

| 函数 | 说明 |
| --- | --- |
| `rand([seed])` | 返回 10⁴~10⁵ 的随机数；`seed` 可选作随机种子 |
| `left()` / `left(x,y)` / `left(x,y,hold_ms)` | 左键；不填坐标 = 跟随鼠标，填 = 固定坐标并**点击后恢复光标**，第三参 = 按住时长 |
| `right(...)` / `middle(...)` | 同上，右键 / 中键 |
| `keyboard("按键")` | 按下按键，如 `"A"`、`"F5"`、`"Ctrl+Shift+K"`、`"Enter"` |
| `pause(ms)` | 等待 n ms，`Ctrl+Alt+F12` 可立即打断 |
| `hotkey("按键")` | 在 `setup()` 里绑定启动热键，如 `"Ctrl+Alt+X"` |
| `mouse()` | 返回当前鼠标坐标 `Coord`（`.x` / `.y`） |
| `point(x,y)` | 构造 `Coord` |
| `stopped()` | 是否已被急停 / 再按热键停止 |

语法就是**标准 C++**：`int` / `for` / `while` / `if else` / `break` / `continue`、自定义函数、
数组、结构体、全部基础运算与逻辑运算符。用户自行编写点击代码，工作台不解释、不限制语法。

`--once` 参数：不注册热键，直接跑一次 `user_code()` 后退出，适合一次性脚本与自动化测试。

## 自己编译工作台

```bat
build_cpp.bat
```

或手动：

```bat
g++ -O2 -s -static -mwindows -std=c++11 studio.cpp ^
    -luser32 -lcomdlg32 -lshell32 -o AutoClickerCPP.exe
python export_template.py
g++ -O2 -s -static -mwindows -std=c++11 runtime_template.cpp -luser32 -o out\AutoClickerScript.exe
```

MSVC：`cl /O2 /MT /EHsc /DUNICODE /D_UNICODE studio.cpp /link user32.lib comdlg32.lib shell32.lib`

> 工作台 exe 约 573 KB（静态链接 C++ 运行库，仍只依赖系统 DLL）；
> 用户脚本产物约 42 KB。两者都远小于 Python onefile 的 ~31 MB。

## 文件清单

| 文件 | 说明 |
| --- | --- |
| `studio.cpp` | 工作台全部源码（单文件），内嵌三段运行时原文 |
| `runtime_template.cpp` | 由 `export_template.py` 从 `studio.cpp` 导出的完整可编译模板，保证只有一份真源 |
| `export_template.py` | 同步导出脚本，升级内置函数后跑一次即可 |
| `build_cpp.bat` | 一键构建工作台 + 模板 + 示例产物 |
| `AutoClickerCPP.exe` | 构建产物（Release 附件，不入库） |
| `out/`、`full_source.cpp`、`compiler.ini` | 运行时生成，已被 `.gitignore` 排除 |

## 与 Python 版的关系（双轨约定）

本仓库有两条**独立升级**的产品线：

| 线 | 目录 | 版本 | Release tag | 产物 |
| --- | --- | --- | --- | --- |
| Python GUI 版 | `src/`、`docs/` | `1.5.x` | `v1.5.x` | `AutoClicker.exe`（完整 GUI / 托盘 / 主题 / 背景图 / 节点循环） |
| C++ 工作台 | `cpp/` | `1.0.x` | `cpp-v1.0.x` | `AutoClickerCPP.exe`（真 C++ 编写 + 真编译 + 极小产物） |

任一条线可以单独发补丁版，不强制另一条同步升级；`CHANGELOG.md` 按产品线分区记录。

- Python 版偏「开箱即用的完整桌面工具」；
- C++ 版偏「自己写一段真 C++ 点击逻辑，得到零依赖极小的 exe」。

旧的 `docs/clicker.cpp` 参考骨架已被 `cpp/runtime_template.cpp` 取代并移除，
`docs/CPP_PATH.md` 保留为迁移说明。

## 已知限制

- 编译需要本机已安装 C++ 编译器；工作台不会自带编译器（否则违背「单文件、极小」目标）。
- 产物是 `SendInput` 系统级模拟点击，以管理员权限运行的程序可能收不到普通权限进程的注入输入。
- 全局热键若被其它程序占用会注册失败；可在 `setup()` 里换键。
- 「数据包级点击模拟」仍只在规划中，不提供代码 / 驱动 / 绕过机制。
