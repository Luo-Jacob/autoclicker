/*
 * AutoClicker C++ Studio 1.0.0
 * 由 Luo-Jacob 制作 · MIT License
 *
 * 第二条产品线: 单文件、零额外依赖的 Win32 GUI「C++ 代码工作台」。
 *
 *   · 编辑器里写的是 **真 C++ 代码**(不是伪代码 / 不是自写 DSL 解释器)
 *   · 初始模板已内置 rand/left/right/middle/keyboard/pause/hotkey/mouse/point
 *   · 点「编译」会把「运行时 + 你的代码」拼成完整 .cpp, 调用本机 g++ / clang++
 *     真正编译成另一个独立 exe(-O2 -s -static -mwindows, 只链系统 user32)
 *   · 编译产物: 无控制台、无 DLL、无 Python、约 50~120 KB
 *   · 编译器需要本机已安装(MinGW-w64 / TDM-GCC / MSVC+clang), 但产物零依赖
 *
 * 编译本工作台 (MinGW-w64 / TDM-GCC):
 *   g++ -O2 -s -static -mwindows -std=c++11 studio.cpp ^
 *       -luser32 -lcomdlg32 -lshell32 -o AutoClickerCPP.exe
 *
 * 编译 (MSVC Developer Command Prompt):
 *   cl /O2 /MT /EHsc /DUNICODE /D_UNICODE studio.cpp ^
 *      /link user32.lib comdlg32.lib shell32.lib /out:AutoClickerCPP.exe
 */

#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0600
#endif
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <commdlg.h>
#include <cstdio>
#include <cstring>
#include <string>

#define CPP_VERSION "1.0.0"

typedef std::wstring WStr;
typedef std::string Str;

/* =========================================================================
 * 内嵌运行时: 写进 merged.cpp 后由 g++ 真正编译, 全部是真 C++ 函数。
 * 用户代码区不需要 #include —— 这些声明与实现已经在前面。
 * ========================================================================= */
static const char* RUNTIME_HEAD = R"ACPP(
/* ---- AutoClicker C++ Runtime 1.0.0 (由工作台自动插入, 无需自己写) ---- */
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0600
#endif
#include <windows.h>
#include <cstdio>
#include <cstring>

static HANDLE g_stopEvent = NULL;
static volatile LONG g_stopping = 0;
static volatile LONG g_running = 0;
static HANDLE g_thread = NULL;
static UINT g_start_mods = MOD_CONTROL | MOD_ALT;
static WORD g_start_vk = 'X';

static bool stopped() {
    return InterlockedCompareExchange(&g_stopping, 0, 0) != 0 ||
           WaitForSingleObject(g_stopEvent, 0) == WAIT_OBJECT_0;
}

static char lc(char c) { return (c >= 'A' && c <= 'Z') ? (char)(c + 32) : c; }
static bool ieq(const char* a, const char* b) {
    while (*a && *b) { if (lc(*a) != lc(*b)) return false; ++a; ++b; }
    return *a == *b;
}
static char* next_token(char* buf, const char* delim, char** save) {
    char* start = buf ? buf : *save;
    if (!start || !*start) return NULL;
    while (*start && *start == *delim) ++start;
    if (!*start) { *save = start; return NULL; }
    char* p = start;
    while (*p && *p != *delim) ++p;
    if (*p) { *p = 0; *save = p + 1; } else { *save = p; }
    return start;
}

struct Coord { long x; long y; };

static Coord mouse() { POINT p = {0,0}; GetCursorPos(&p); Coord c = {p.x,p.y}; return c; }
static Coord point(long x, long y) { Coord c = {x,y}; return c; }

/* 自带 LCG, 不 include <cstdlib>, 避免与 rand 重名 */
static unsigned long long g_rng = 88172645463393265ULL;
static int ac_rand(int seed = -1) {
    if (seed >= 0) g_rng = (unsigned long long)seed ^ GetTickCount64();
    g_rng = g_rng * 6364136223846793005ULL + 1442695040888963407ULL;
    return 10000 + (int)((g_rng >> 33) % 90001ULL);   /* 10^4 ~ 10^5 */
}
#define rand ac_rand

static void send_mouse(DWORD down, DWORD up, Coord c, bool fixed, int hold_ms) {
    if (stopped()) return;
    POINT old = {0,0};
    GetCursorPos(&old);
    if (fixed) SetCursorPos((int)c.x, (int)c.y);
    INPUT a = {0}, b = {0};
    a.type = INPUT_MOUSE; a.mi.dwFlags = down;
    b.type = INPUT_MOUSE; b.mi.dwFlags = up;
    SendInput(1, &a, sizeof(INPUT));
    if (hold_ms > 0) WaitForSingleObject(g_stopEvent, (DWORD)hold_ms);
    SendInput(1, &b, sizeof(INPUT));
    if (fixed) SetCursorPos(old.x, old.y);
}

static void left()                       { send_mouse(MOUSEEVENTF_LEFTDOWN,   MOUSEEVENTF_LEFTUP,   mouse(), false, 0); }
static void left(Coord c)                { send_mouse(MOUSEEVENTF_LEFTDOWN,   MOUSEEVENTF_LEFTUP,   c, true, 0); }
static void left(long x, long y)         { left(point(x,y)); }
static void left(long x, long y, int h)  { send_mouse(MOUSEEVENTF_LEFTDOWN,   MOUSEEVENTF_LEFTUP,   point(x,y), true, h); }
static void right()                      { send_mouse(MOUSEEVENTF_RIGHTDOWN,  MOUSEEVENTF_RIGHTUP,  mouse(), false, 0); }
static void right(Coord c)               { send_mouse(MOUSEEVENTF_RIGHTDOWN,  MOUSEEVENTF_RIGHTUP,  c, true, 0); }
static void right(long x, long y)        { right(point(x,y)); }
static void right(long x, long y, int h) { send_mouse(MOUSEEVENTF_RIGHTDOWN,  MOUSEEVENTF_RIGHTUP,  point(x,y), true, h); }
static void middle()                     { send_mouse(MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, mouse(), false, 0); }
static void middle(Coord c)              { send_mouse(MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, c, true, 0); }
static void middle(long x, long y)       { middle(point(x,y)); }
static void middle(long x, long y, int h){ send_mouse(MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, point(x,y), true, h); }

static WORD vk_of(const char* s) {
    if (!s || !*s) return 0;
    if (ieq(s,"ENTER")) return VK_RETURN;
    if (ieq(s,"ESC") || ieq(s,"ESCAPE")) return VK_ESCAPE;
    if (ieq(s,"SPACE")) return VK_SPACE;
    if (ieq(s,"TAB")) return VK_TAB;
    if (ieq(s,"UP")) return VK_UP;
    if (ieq(s,"DOWN")) return VK_DOWN;
    if (ieq(s,"LEFT")) return VK_LEFT;
    if (ieq(s,"RIGHT")) return VK_RIGHT;
    if (s[0]=='F' && s[1]>='0' && s[1]<='9') {
        int n = 0;
        for (const char* p=s+1; *p>='0' && *p<='9'; ++p) n=n*10+(*p-'0');
        if (n>=1 && n<=12) return (WORD)(VK_F1+n-1);
    }
    if (s[1]==0) { SHORT r=VkKeyScanA(s[0]); if (r!=-1) return (WORD)(r & 0xFF); }
    return 0;
}

static void keyboard(const char* spec) {
    if (stopped() || !spec) return;
    char buf[128]; snprintf(buf,sizeof(buf),"%s",spec);
    INPUT seq[8]; int n=0;
    char* save=NULL; char* tok=next_token(buf,"+",&save);
    while (tok && n<7) {
        WORD vk=0;
        if (ieq(tok,"CTRL")||ieq(tok,"CONTROL")) vk=VK_CONTROL;
        else if (ieq(tok,"ALT")) vk=VK_MENU;
        else if (ieq(tok,"SHIFT")) vk=VK_SHIFT;
        else if (ieq(tok,"WIN")) vk=VK_LWIN;
        else vk=vk_of(tok);
        if (vk) { ZeroMemory(&seq[n],sizeof(INPUT)); seq[n].type=INPUT_KEYBOARD; seq[n].ki.wVk=vk; ++n; }
        tok=next_token(NULL,"+",&save);
    }
    if (!n) return;
    SendInput(n,seq,sizeof(INPUT));
    for (int i=n-1;i>=0;--i) { seq[i].ki.dwFlags=KEYEVENTF_KEYUP; SendInput(1,&seq[i],sizeof(INPUT)); }
}

static void pause(int ms) { if (ms>0) WaitForSingleObject(g_stopEvent,(DWORD)ms); }

/* 绑定启动/停止热键; 在 setup() 里调用。再按一次热键即停止。 */
static int hotkey(const char* spec) {
    UINT mods=0; WORD vk=0;
    char buf[128]; snprintf(buf,sizeof(buf),"%s",spec?spec:"");
    char* save=NULL; char* tok=next_token(buf,"+",&save);
    while (tok) {
        if (ieq(tok,"CTRL")||ieq(tok,"CONTROL")) mods|=MOD_CONTROL;
        else if (ieq(tok,"ALT")) mods|=MOD_ALT;
        else if (ieq(tok,"SHIFT")) mods|=MOD_SHIFT;
        else if (ieq(tok,"WIN")) mods|=MOD_WIN;
        else vk=vk_of(tok);
        tok=next_token(NULL,"+",&save);
    }
    if (!vk) return 0;
    g_start_mods=mods; g_start_vk=vk;
    return 1;
}

static void setup();
static void user_code();
)ACPP";

/* 编辑器初始代码 —— 用户看到并修改的就是这一段真 C++ */
static const char* DEFAULT_CODE = R"ACPP(
/* ==================== 你的代码区 (真 C++, 不是伪代码) ====================
 * 内置函数(已由工作台自动提供, 无需 #include):
 *   rand([seed])              返回 10^4 ~ 10^5 的随机数; seed 可选
 *   left() / left(x,y)        左键; 不填坐标=跟随鼠标, 填=固定坐标并自动恢复光标
 *   right() / right(x,y)      右键
 *   middle() / middle(x,y)    中键
 *   left(x,y,hold_ms)         第三参=按住时长(ms), 三个键都支持
 *   keyboard("按键")          按下按键, 如 "A" / "F5" / "Ctrl+Shift+K" / "Enter"
 *   pause(ms)                 等待 n ms (Ctrl+Alt+F12 可立即打断)
 *   hotkey("按键")            绑定启动热键, 在 setup() 里调用
 *   mouse()                   返回当前鼠标坐标 Coord, 可用 .x / .y
 *   point(x,y)                构造 Coord
 *   stopped()                 是否已被急停 / 再按热键停止
 *
 * 语法: 标准 C++ —— int / for / while / if else / break / continue /
 *       自定义函数 / 数组 / 结构体 / 全部基础运算与逻辑运算符。
 * 启动: 按 setup() 里绑定的热键开始, 再按一次停止; Ctrl+Alt+F12 随时急停退出。
 * ==================================================================== */

static void setup() {
    hotkey("Ctrl+Alt+X");          /* 启动 / 停止热键 */
}

static void user_code() {
    int times = rand(2026) % 20 + 10;      /* 随机 10~29 次 */
    Coord p = mouse();

    for (int i = 0; i < times && !stopped(); ++i) {
        if (i % 3 == 0) {
            keyboard("F5");                /* 每 3 次按一下 F5 */
        } else if (i % 2 == 0) {
            left();                        /* 跟随鼠标点击 */
        } else {
            right(320, 240);               /* 固定坐标点击 */
        }
        pause(120);                        /* 120ms 间隔 */
    }
}
)ACPP";

static const char* RUNTIME_TAIL = R"ACPP(
/* ---- 运行时主循环 (由工作台自动插入) ---- */
static DWORD WINAPI ac_worker(LPVOID) {
    user_code();
    InterlockedExchange(&g_running, 0);
    return 0;
}

static void ac_start() {
    if (g_running) return;
    if (g_thread) { WaitForSingleObject(g_thread,0); CloseHandle(g_thread); g_thread=NULL; }
    ResetEvent(g_stopEvent);
    InterlockedExchange(&g_stopping,0);
    InterlockedExchange(&g_running,1);
    g_thread = CreateThread(NULL,0,ac_worker,NULL,0,NULL);
    if (!g_thread) InterlockedExchange(&g_running,0);
}

static void ac_stop() {
    InterlockedExchange(&g_stopping,1);
    SetEvent(g_stopEvent);
    if (g_thread) { WaitForSingleObject(g_thread,3000); CloseHandle(g_thread); g_thread=NULL; }
    InterlockedExchange(&g_running,0);
}

static bool has_arg(const wchar_t* a) {
    const wchar_t* c = GetCommandLineW();
    for (const wchar_t* p = c; *p; ++p) {
        const wchar_t* q = a, * r = p;
        while (*q && *r && *q == *r) { ++q; ++r; }
        if (!*q) return true;
    }
    return false;
}

int WINAPI WinMain(HINSTANCE, HINSTANCE, LPSTR, int) {
    g_stopEvent = CreateEventW(NULL, TRUE, FALSE, NULL);
    if (!g_stopEvent) return 1;

    setup();                                        /* 用户在 setup() 里绑定热键 */

    /* --once: 不注册热键, 直接跑一次 user_code() 后退出(适合一次性脚本 / 自动化测试) */
    if (has_arg(L"--once")) {
        ac_start();
        if (g_thread) { WaitForSingleObject(g_thread, 60000); CloseHandle(g_thread); g_thread = NULL; }
        CloseHandle(g_stopEvent);
        return 0;
    }

    RegisterHotKey(NULL, 0x0FFE, g_start_mods | MOD_NOREPEAT, g_start_vk);
    RegisterHotKey(NULL, 0x0FFF, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_F12);

    MSG msg;
    while (GetMessageW(&msg, NULL, 0, 0) > 0) {
        if (msg.message == WM_HOTKEY) {
            if (msg.wParam == 0x0FFF) { ac_stop(); break; }   /* 急停并退出 */
            if (msg.wParam == 0x0FFE) {
                if (g_running) ac_stop(); else ac_start();      /* 再按一次停止 */
            }
        }
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    ac_stop();
    UnregisterHotKey(NULL, 0x0FFE);
    UnregisterHotKey(NULL, 0x0FFF);
    CloseHandle(g_stopEvent);
    return 0;
}
)ACPP";

/* =========================================================================
 * 工作台本体
 * ========================================================================= */
#define ID_EDIT      101
#define ID_LOG       102
#define ID_STATUS    103
#define ID_NEW       201
#define ID_OPEN      202
#define ID_SAVE      203
#define ID_FULL      204
#define ID_COMPILER  205
#define ID_ABOUT     206
#define ID_BUILD_RUN 207
#define ID_BUILD     208
#define ID_RUN       209
#define ID_STOP      210
#define ID_CLEAR     211
#define ID_HELP      212

static HWND g_hwnd = NULL, g_edit = NULL, g_log = NULL, g_status = NULL;
static HFONT g_font_ui = NULL, g_font_code = NULL;
static WStr g_compiler, g_out_exe, g_code_path, g_last_src;
static HANDLE g_child = NULL;

static WStr U8(const char* s) {
    int n = MultiByteToWideChar(CP_UTF8, 0, s, -1, NULL, 0);
    if (n <= 1) return WStr();
    WStr r((size_t)n - 1, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s, -1, &r[0], n);
    return r;
}

static Str W8(const WStr& s) {
    if (s.empty()) return Str();
    int n = WideCharToMultiByte(CP_UTF8, 0, s.c_str(), -1, NULL, 0, NULL, NULL);
    if (n <= 1) return Str();
    Str r((size_t)n - 1, '\0');
    WideCharToMultiByte(CP_UTF8, 0, s.c_str(), -1, &r[0], n, NULL, NULL);
    return r;
}

static bool file_exists(const WStr& p) {
    return p.empty() ? false :
           GetFileAttributesW(p.c_str()) != INVALID_FILE_ATTRIBUTES;
}

static WStr exedir() {
    wchar_t buf[MAX_PATH + 1] = {0};
    GetModuleFileNameW(NULL, buf, MAX_PATH);
    WStr p(buf);
    size_t i = p.find_last_of(L"\\/");
    return (i == WStr::npos) ? WStr(L".") : p.substr(0, i);
}

static WStr ini_path() { return exedir() + L"\\compiler.ini"; }

static WStr load_setting() {
    wchar_t buf[MAX_PATH + 1] = {0};
    GetPrivateProfileStringW(L"cpp", L"compiler", L"", buf, MAX_PATH, ini_path().c_str());
    return WStr(buf);
}

static void save_setting(const WStr& v) {
    WritePrivateProfileStringW(L"cpp", L"compiler", v.c_str(), ini_path().c_str());
}

static WStr detect_compiler() {
    WStr s = load_setting();
    if (file_exists(s)) return s;

    wchar_t buf[MAX_PATH + 1] = {0};
    if (SearchPathW(NULL, L"g++.exe", NULL, MAX_PATH, buf, NULL))
        return WStr(buf);
    if (SearchPathW(NULL, L"clang++.exe", NULL, MAX_PATH, buf, NULL))
        return WStr(buf);

    static const wchar_t* cand[] = {
        L"C:\\Program Files (x86)\\Dev-Cpp\\MinGW64\\bin\\g++.exe",
        L"C:\\Dev-Cpp\\MinGW64\\bin\\g++.exe",
        L"C:\\msys64\\mingw64\\bin\\g++.exe",
        L"C:\\mingw64\\bin\\g++.exe",
        L"C:\\TDM-GCC-64\\bin\\g++.exe",
        L"C:\\Program Files\\LLVM\\bin\\clang++.exe",
        L"C:\\Program Files (x86)\\Microsoft Visual Studio\\2022\\BuildTools\\VC\\Tools\\Llvm\\x64\\bin\\clang++.exe",
        NULL
    };
    for (int i = 0; cand[i]; ++i)
        if (file_exists(cand[i])) return WStr(cand[i]);
    return WStr();
}

static void set_status(const WStr& s) {
    if (g_status) SetWindowTextW(g_status, s.c_str());
}

static void log_line(const WStr& s) {
    if (!g_log) return;
    int n = GetWindowTextLengthW(g_log);
    WStr old(n, L'\0');
    if (n > 0) GetWindowTextW(g_log, &old[0], n + 1);
    WStr all = old + s + L"\r\n";
    SetWindowTextW(g_log, all.c_str());
    SendMessageW(g_log, EM_LINESCROLL, 0, 100000);
}

static bool write_utf8(const WStr& path, const Str& data) {
    HANDLE f = CreateFileW(path.c_str(), GENERIC_WRITE, 0, NULL,
                           CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE) return false;
    DWORD wrote = 0;
    BOOL ok = WriteFile(f, data.data(), (DWORD)data.size(), &wrote, NULL);
    CloseHandle(f);
    return ok && wrote == data.size();
}

static Str read_utf8(const WStr& path) {
    HANDLE f = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, NULL,
                           OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE) return Str();
    DWORD sz = GetFileSize(f, NULL), got = 0;
    if (sz == INVALID_FILE_SIZE || sz > 4 * 1024 * 1024) { CloseHandle(f); return Str(); }
    Str r((size_t)sz, '\0');
    if (sz) ReadFile(f, &r[0], sz, &got, NULL);
    CloseHandle(f);
    r.resize(got);
    return r;
}

static Str editor_text() {
    int n = GetWindowTextLengthW(g_edit);
    if (n <= 0) return Str();
    WStr s((size_t)n, L'\0');
    GetWindowTextW(g_edit, &s[0], n + 1);
    return W8(s);
}

static void set_editor(const Str& s) {
    SetWindowTextW(g_edit, U8(s.c_str()).c_str());
}

static Str merged_source(const Str& code) {
    return Str(RUNTIME_HEAD) + code + Str(RUNTIME_TAIL);
}

static WStr temp_dir() {
    wchar_t tmp[MAX_PATH + 1] = {0};
    GetTempPathW(MAX_PATH, tmp);
    wchar_t num[32] = {0};
    wsprintfW(num, L"%lu", (unsigned long)GetTickCount());
    WStr d = WStr(tmp) + L"AutoClickerCPP_" + num;
    if (GetFileAttributesW(d.c_str()) == INVALID_FILE_ATTRIBUTES)
        CreateDirectoryW(d.c_str(), NULL);
    return d;
}

static bool ensure_dir(const WStr& dir) {
    if (GetFileAttributesW(dir.c_str()) != INVALID_FILE_ATTRIBUTES) return true;
    return CreateDirectoryW(dir.c_str(), NULL) != FALSE;
}

static WStr out_path() {
    if (!g_out_exe.empty()) return g_out_exe;
    return exedir() + L"\\out\\AutoClickerScript.exe";
}

static WStr compiler_args(const WStr& src, const WStr& out) {
    bool clang = g_compiler.find(L"clang") != WStr::npos;
    WStr link = clang ? L"-Wl,--subsystem,windows" : L"-mwindows";
    return L"\"" + g_compiler + L"\" -O2 -s -static " + link +
           L" -std=c++11 \"" + src + L"\" -luser32 -o \"" + out + L"\"";
}

/* std::to_wstring 在老 MinGW 上可能缺失, 统一用 wsprintfW 生成数字 */
static WStr num_wstr(unsigned long v) {
    wchar_t b[32] = {0};
    wsprintfW(b, L"%lu", v);
    return WStr(b);
}

static void stop_child() {
    if (g_child) {
        TerminateProcess(g_child, 0);
        CloseHandle(g_child);
        g_child = NULL;
        log_line(U8("[运行] 已终止正在运行的脚本"));
    }
}

static void run_exe() {
    WStr out = out_path();
    if (!file_exists(out)) {
        log_line(U8("[运行] 还没有编译产物, 请先点「编译」"));
        return;
    }
    stop_child();
    STARTUPINFOW si = {0}; si.cb = sizeof(si);
    PROCESS_INFORMATION pi = {0};
    WStr cmd = L"\"" + out + L"\"";
    WStr dir = exedir();
    BOOL ok = CreateProcessW(out.c_str(), &cmd[0], NULL, NULL, FALSE,
                             0, NULL, dir.c_str(), &si, &pi);
    if (!ok) {
        log_line(U8("[运行] 启动失败, GetLastError=") + num_wstr(GetLastError()));
        return;
    }
    CloseHandle(pi.hThread);
    g_child = pi.hProcess;
    log_line(U8("[运行] 已启动: ") + out);
    log_line(U8("        按 Ctrl+Alt+X 开始/停止, Ctrl+Alt+F12 急停退出"));
}

static void do_compile(bool run_after) {
    if (g_compiler.empty() || !file_exists(g_compiler)) {
        log_line(U8("[编译] 未找到 C++ 编译器, 请点「编译器…」手动指定 g++.exe / clang++.exe"));
        set_status(U8("状态: 未配置编译器"));
        return;
    }
    Str code = editor_text();
    if (code.empty()) {
        log_line(U8("[编译] 代码区为空"));
        return;
    }
    WStr dir = temp_dir();
    WStr src = dir + L"\\merged.cpp";
    WStr logf = dir + L"\\build.log";
    WStr out = out_path();
    WStr outdir = out.substr(0, out.find_last_of(L"\\/"));
    if (!outdir.empty()) ensure_dir(outdir);

    if (!write_utf8(src, merged_source(code))) {
        log_line(U8("[编译] 无法写入临时源码"));
        return;
    }
    g_last_src = src;

    WStr cmd = compiler_args(src, out);
    log_line(U8("===== 编译 ====="));
    log_line(cmd);
    set_status(U8("状态: 正在编译…"));

    SECURITY_ATTRIBUTES sa = {0};
    sa.nLength = sizeof(sa); sa.bInheritHandle = TRUE; sa.lpSecurityDescriptor = NULL;
    HANDLE hlog = CreateFileW(logf.c_str(), GENERIC_WRITE, FILE_SHARE_READ, &sa,
                              CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (hlog == INVALID_HANDLE_VALUE) {
        log_line(U8("[编译] 无法创建日志文件"));
        return;
    }

    STARTUPINFOW si = {0}; si.cb = sizeof(si);
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdOutput = hlog; si.hStdError = hlog;
    si.hStdInput = NULL;
    PROCESS_INFORMATION pi = {0};
    WStr work = dir;
    BOOL started = CreateProcessW(g_compiler.c_str(), &cmd[0], NULL, NULL, TRUE,
                                  0, NULL, work.c_str(), &si, &pi);
    if (!started) {
        CloseHandle(hlog);
        log_line(U8("[编译] 无法启动编译器, GetLastError=") + num_wstr(GetLastError()));
        set_status(U8("状态: 编译器启动失败"));
        return;
    }
    WaitForSingleObject(pi.hProcess, 120000);
    DWORD ec = 1;
    GetExitCodeProcess(pi.hProcess, &ec);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    CloseHandle(hlog);

    Str logtext = read_utf8(logf);
    if (!logtext.empty()) log_line(U8(logtext.c_str()));

    if (ec == 0 && file_exists(out)) {
        WIN32_FILE_ATTRIBUTE_DATA fa = {0};
        GetFileAttributesExW(out.c_str(), GetFileExInfoStandard, &fa);
        unsigned long kb = (fa.nFileSizeHigh ? 0xffffffffUL : fa.nFileSizeLow) / 1024;
        log_line(U8("[编译] 成功 → ") + out + U8("  (") + num_wstr(kb) + U8(" KB)"));
        set_status(U8("状态: 编译成功 · 零依赖单文件 exe · ") + num_wstr(kb) + U8(" KB"));
        if (run_after) run_exe();
    } else {
        log_line(U8("[编译] 失败 (exit=") + num_wstr(ec) + U8("), 请按上面的报错修改代码"));
        set_status(U8("状态: 编译失败"));
    }
}

static bool pick_file(bool saving, const wchar_t* filter, WStr& out) {
    wchar_t buf[MAX_PATH + 1] = {0};
    OPENFILENAMEW of = {0};
    of.lStructSize = sizeof(of);
    of.hwndOwner = g_hwnd;
    of.lpstrFilter = filter;
    of.lpstrFile = buf;
    of.nMaxFile = MAX_PATH;
    of.lpstrDefExt = saving ? L"cpp" : NULL;
    of.Flags = OFN_EXPLORER | OFN_NOCHANGEDIR |
               (saving ? OFN_OVERWRITEPROMPT : OFN_FILEMUSTEXIST);
    BOOL ok = saving ? GetSaveFileNameW(&of) : GetOpenFileNameW(&of);
    if (ok) out = WStr(buf);
    return ok != FALSE;
}

static void open_code() {
    WStr p;
    if (!pick_file(false, L"C++ 源码 (*.cpp)\0*.cpp\0所有文件\0*.*\0\0", p)) return;
    Str s = read_utf8(p);
    if (s.empty()) { log_line(U8("[文件] 读取失败或文件为空")); return; }
    set_editor(s);
    g_code_path = p;
    log_line(U8("[文件] 已打开: ") + p);
    set_status(U8("状态: 已打开 ") + p);
}

static void save_code(bool as) {
    WStr p = g_code_path;
    if (as || p.empty()) {
        if (!pick_file(true, L"C++ 源码 (*.cpp)\0*.cpp\0\0", p)) return;
        g_code_path = p;
    }
    if (!write_utf8(p, editor_text())) {
        log_line(U8("[文件] 保存失败"));
        return;
    }
    log_line(U8("[文件] 已保存: ") + p);
    set_status(U8("状态: 已保存 ") + p);
}

static void export_full() {
    WStr p = exedir() + L"\\full_source.cpp";
    if (!write_utf8(p, merged_source(editor_text()))) {
        log_line(U8("[源码] 导出失败"));
        return;
    }
    log_line(U8("[源码] 已导出完整可编译源码: ") + p);
    log_line(U8("        这就是真正交给 g++ 编译的文件(运行时 + 你的代码 + 主循环)"));
    ShellExecuteW(g_hwnd, L"open", p.c_str(), NULL, NULL, SW_SHOW);
    set_status(U8("状态: 已导出完整源码"));
}

static void choose_compiler() {
    WStr p;
    if (!pick_file(false, L"编译器 (g++.exe / clang++.exe)\0*.exe\0所有文件\0*.*\0\0", p)) return;
    if (!file_exists(p)) return;
    g_compiler = p;
    save_setting(p);
    log_line(U8("[编译器] 已设置: ") + p);
    set_status(U8("编译器: ") + p);
}

static void show_help() {
    WStr s = U8("写的是真 C++ 代码(不是伪代码), 点击「编译」由 g++/clang++ 真正编译。\r\n\r\n") +
        U8("内置函数:\r\n") +
        U8("  rand([seed])          10^4~10^5 随机数\r\n") +
        U8("  left() / left(x,y)    左键(可带第三参按住 ms)\r\n") +
        U8("  right() / right(x,y)  右键\r\n") +
        U8("  middle() / middle(x,y) 中键\r\n") +
        U8("  keyboard(\"按键\")      A / F5 / Ctrl+Shift+K / Enter\r\n") +
        U8("  pause(ms)             可被急停打断的等待\r\n") +
        U8("  hotkey(\"按键\")        在 setup() 里绑定启动热键\r\n") +
        U8("  mouse() / point(x,y)  当前坐标 / 构造坐标\r\n") +
        U8("  stopped()             是否已停止\r\n\r\n") +
        U8("语法: int / for / while / if else / break / continue / 自定义函数 /\r\n") +
        U8("      数组 / 结构体 / 全部基础运算与逻辑运算符(标准 C++)。\r\n\r\n") +
        U8("运行: 按 setup() 绑定的热键开始, 再按一次停止;\r\n") +
        U8("      Ctrl+Alt+F12 随时急停并退出脚本进程。\r\n\r\n") +
        U8("「完整源码」可导出真正交给编译器的合并后 .cpp 查看。");
    MessageBoxW(g_hwnd, s.c_str(), U8("帮助 / 函数速查").c_str(),
                MB_OK | MB_ICONINFORMATION);
}

static void show_about() {
    WStr s = U8("AutoClicker C++ Studio  v") + U8(CPP_VERSION) +
             U8("\r\n由 Luo-Jacob 制作 · MIT License\r\n\r\n") +
             U8("编辑器里写的是真 C++ 代码, 点「编译」会调用本机 g++/clang++ 真正编译。\r\n") +
             U8("产物: 无控制台、零 DLL 依赖的单文件 exe(约 50~120 KB)。\r\n\r\n") +
             U8("https://github.com/Luo-Jacob/autoclicker");
    MessageBoxW(g_hwnd, s.c_str(), U8("关于").c_str(), MB_OK | MB_ICONINFORMATION);
}

static HWND make_button(const wchar_t* cls, const char* text, int id, HINSTANCE hi) {
    HWND h = CreateWindowExW(0, cls, U8(text).c_str(),
                             WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON,
                             0, 0, 10, 10, g_hwnd, (HMENU)(INT_PTR)id, hi, NULL);
    if (h) SendMessageW(h, WM_SETFONT, (WPARAM)g_font_ui, TRUE);
    return h;
}

static void layout(int W, int H) {
    const int pad = 10, gap = 6, bh = 30;
    int bw = (W - 2 * pad - 5 * gap) / 6;
    if (bw < 60) bw = 60;
    static const int ids1[] = { ID_NEW, ID_OPEN, ID_SAVE, ID_FULL, ID_COMPILER, ID_ABOUT };
    static const int ids2[] = { ID_BUILD_RUN, ID_BUILD, ID_RUN, ID_STOP, ID_CLEAR, ID_HELP };
    for (int i = 0; i < 6; ++i) {
        int x = pad + i * (bw + gap);
        SetWindowPos(GetDlgItem(g_hwnd, ids1[i]), NULL, x, pad, bw, bh,
                     SWP_NOZORDER | SWP_SHOWWINDOW);
        SetWindowPos(GetDlgItem(g_hwnd, ids2[i]), NULL, x, pad + bh + gap, bw, bh,
                     SWP_NOZORDER | SWP_SHOWWINDOW);
    }
    int top = pad + 2 * bh + gap + 8;
    int statusH = 26, logH = 140;
    int editH = H - top - logH - statusH - 3 * gap;
    if (editH < 80) editH = 80;
    SetWindowPos(g_edit, NULL, pad, top, W - 2 * pad, editH, SWP_NOZORDER | SWP_SHOWWINDOW);
    SetWindowPos(g_log, NULL, pad, top + editH + gap, W - 2 * pad, logH,
                 SWP_NOZORDER | SWP_SHOWWINDOW);
    SetWindowPos(g_status, NULL, pad, H - statusH - 6, W - 2 * pad, statusH - 4,
                 SWP_NOZORDER | SWP_SHOWWINDOW);
}

static LRESULT CALLBACK WndProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
    case WM_CREATE: {
        HINSTANCE hi = ((LPCREATESTRUCTW)l)->hInstance;
        g_font_ui = CreateFontW(18, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET,
                                OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                                CLEARTYPE_QUALITY, DEFAULT_PITCH | FF_DONTCARE,
                                L"Microsoft YaHei UI");
        g_font_code = CreateFontW(19, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET,
                                  OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                                  CLEARTYPE_QUALITY, FIXED_PITCH | FF_DONTCARE,
                                  L"Consolas");
        make_button(L"BUTTON", "新建",     ID_NEW,       hi);
        make_button(L"BUTTON", "打开",     ID_OPEN,      hi);
        make_button(L"BUTTON", "保存",     ID_SAVE,      hi);
        make_button(L"BUTTON", "完整源码", ID_FULL,      hi);
        make_button(L"BUTTON", "编译器…",  ID_COMPILER,  hi);
        make_button(L"BUTTON", "关于",     ID_ABOUT,     hi);
        make_button(L"BUTTON", "编译并运行", ID_BUILD_RUN, hi);
        make_button(L"BUTTON", "编译",     ID_BUILD,     hi);
        make_button(L"BUTTON", "运行",     ID_RUN,       hi);
        make_button(L"BUTTON", "停止运行", ID_STOP,      hi);
        make_button(L"BUTTON", "清空日志", ID_CLEAR,     hi);
        make_button(L"BUTTON", "帮助",     ID_HELP,      hi);

        DWORD es = WS_CHILD | WS_VISIBLE | WS_VSCROLL | WS_HSCROLL |
                   ES_MULTILINE | ES_AUTOVSCROLL | ES_AUTOHSCROLL | ES_WANTRETURN;
        g_edit = CreateWindowExW(WS_EX_CLIENTEDGE, L"EDIT", L"", es,
                                 0, 0, 10, 10, h, (HMENU)(INT_PTR)ID_EDIT, hi, NULL);
        g_log = CreateWindowExW(WS_EX_CLIENTEDGE, L"EDIT", L"",
                                es | ES_READONLY, 0, 0, 10, 10, h,
                                (HMENU)(INT_PTR)ID_LOG, hi, NULL);
        g_status = CreateWindowExW(0, L"STATIC", L"",
                                   WS_CHILD | WS_VISIBLE | SS_LEFT, 0, 0, 10, 10, h,
                                   (HMENU)(INT_PTR)ID_STATUS, hi, NULL);
        SendMessageW(g_edit, WM_SETFONT, (WPARAM)g_font_code, TRUE);
        SendMessageW(g_log, WM_SETFONT, (WPARAM)g_font_code, TRUE);
        SendMessageW(g_status, WM_SETFONT, (WPARAM)g_font_ui, TRUE);
        set_editor(Str(DEFAULT_CODE));
        g_compiler = detect_compiler();
        log_line(U8("AutoClicker C++ Studio v") + U8(CPP_VERSION) +
                 U8(" —— 真 C++ 编写 / 真编译 / 零依赖产物"));
        if (g_compiler.empty()) {
            log_line(U8("[提示] 未自动找到编译器, 请点「编译器…」选择 g++.exe 或 clang++.exe"));
            set_status(U8("状态: 未配置编译器"));
        } else {
            log_line(U8("[编译器] ") + g_compiler);
            set_status(U8("编译器: ") + g_compiler);
        }
        log_line(U8("[说明] 编辑器里是真 C++ 代码; 编译时会把内置运行时与你的代码合并后交给 g++。"));
        log_line(U8("[说明] 按 Ctrl+Alt+F12 可随时急停正在运行的脚本。"));
        return 0;
    }
    case WM_SIZE:
        layout(LOWORD(l), HIWORD(l));
        return 0;
    case WM_COMMAND:
        switch (LOWORD(w)) {
        case ID_NEW:       set_editor(Str(DEFAULT_CODE)); g_code_path.clear();
                           set_status(U8("状态: 已载入初始模板")); break;
        case ID_OPEN:      open_code(); break;
        case ID_SAVE:      save_code(false); break;
        case ID_FULL:      export_full(); break;
        case ID_COMPILER:  choose_compiler(); break;
        case ID_ABOUT:     show_about(); break;
        case ID_BUILD_RUN: do_compile(true); break;
        case ID_BUILD:     do_compile(false); break;
        case ID_RUN:       run_exe(); break;
        case ID_STOP:      stop_child(); break;
        case ID_CLEAR:     SetWindowTextW(g_log, L""); break;
        case ID_HELP:      show_help(); break;
        }
        return 0;
    case WM_CTLCOLORSTATIC: {
        HDC dc = (HDC)w;
        SetBkColor(dc, RGB(244, 246, 249));
        SetTextColor(dc, RGB(37, 47, 63));
        static HBRUSH br = CreateSolidBrush(RGB(244, 246, 249));
        return (LRESULT)br;
    }
    case WM_CLOSE:
        stop_child();
        DestroyWindow(h);
        return 0;
    case WM_DESTROY:
        if (g_font_ui) DeleteObject(g_font_ui);
        if (g_font_code) DeleteObject(g_font_code);
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(h, m, w, l);
}

int WINAPI WinMain(HINSTANCE hi, HINSTANCE, LPSTR, int) {
    WNDCLASSEXW wc = {0};
    wc.cbSize = sizeof(wc);
    wc.lpfnWndProc = WndProc;
    wc.hInstance = hi;
    wc.hCursor = LoadCursorW(NULL, (LPCWSTR)IDC_ARROW);
    wc.hIcon = LoadIconW(NULL, (LPCWSTR)IDI_APPLICATION);
    wc.hbrBackground = (HBRUSH)(COLOR_BTNFACE + 1);
    wc.lpszClassName = L"AutoClickerCPPStudioClass";
    if (!RegisterClassExW(&wc)) return 1;

    WStr title = U8("AutoClicker C++ Studio  v") + U8(CPP_VERSION);
    HWND h = CreateWindowExW(0, L"AutoClickerCPPStudioClass", title.c_str(),
                             WS_OVERLAPPEDWINDOW, CW_USEDEFAULT, CW_USEDEFAULT,
                             1040, 720, NULL, NULL, hi, NULL);
    if (!h) return 1;
    g_hwnd = h;
    ShowWindow(h, SW_SHOW);
    UpdateWindow(h);
    MSG msg;
    while (GetMessageW(&msg, NULL, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    return 0;
}
