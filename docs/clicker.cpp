/*
 * clicker.cpp —— AutoClicker 高级版: 单文件、零额外依赖的 C++ 骨架
 *
 * 由 Luo-Jacob 制作 · MIT License
 *
 * 目标: 一个 .cpp 直接编译成一个 .exe, 不需要 DLL、不需要运行时库安装包、
 *       不需要 Python、不需要任何第三方依赖。只链接 Windows 自带的 user32。
 *
 * 用户只需要改下面的 user_code(), 用这些内置函数编写点击逻辑:
 *
 *   rand(seed)          返回 10^4 ~ 10^5 的随机数 (seed 可选, 作随机种子)
 *   left(坐标)          左键点击; 不填坐标 = 跟随当前鼠标
 *   right(坐标)         右键点击
 *   middle(坐标)        中键点击
 *   keyboard(*按键)     按下按键, 如 "A" / "Ctrl+Shift+F5" / "Enter"
 *   pause(*ms)          等待 n 毫秒 (可被急停立即打断)
 *   hotkey(*按键)       注册全局启动热键, 如 "Ctrl+Alt+X"
 *   mouse()             返回当前鼠标坐标 Coord, 可用 .x / .y
 *
 *   可用语法: int / for / while / if else / break / continue
 *             + - * / %  == != < <= > >=  && || !  ++ -- += -= *= /=
 *
 * 无头文件: 用户代码不需要写任何 #include。
 * 全局急停: Ctrl+Alt+F12 (不可关闭), SetEvent 会打断 pause 与所有点击。
 *
 * ---------------------------------------------------------------------------
 * 编译 (MSVC, Developer Command Prompt, 静态 CRT 以便零依赖):
 *   cl /O2 /MT /EHsc /DUNICODE /D_UNICODE clicker.cpp /link user32.lib /out:clicker.exe
 *
 * 编译 (MinGW-w64):
 *   g++ -O2 -s -static -mwindows clicker.cpp -luser32 -o clicker.exe
 *
 * 典型体积: 80 KB ~ 200 KB (对比 Python + PyInstaller onefile 约 31 MB)。
 * ---------------------------------------------------------------------------
 */

#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0600
#endif
#include <windows.h>
#include <cstdio>
#include <ctime>
#include <cstring>

/* 注意: 故意不 include <cstdlib>, 避免与内置的 rand() 重名 */

static HANDLE g_stopEvent = NULL;
static volatile LONG g_stopping = 0;

static bool stopped() {
    return InterlockedCompareExchange(&g_stopping, 0, 0) != 0 ||
           WaitForSingleObject(g_stopEvent, 0) == WAIT_OBJECT_0;
}

/* 零依赖的大小写无关比较与分词 (MSVC / MinGW 通用) */
static char lc(char c) { return (c >= 'A' && c <= 'Z') ? (char)(c + 32) : c; }
static bool ieq(const char* a, const char* b) {
    while (*a && *b) {
        if (lc(*a) != lc(*b)) return false;
        ++a; ++b;
    }
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

/* ----------------------------- 坐标 ----------------------------- */
struct Coord {
    long x;
    long y;
};

static Coord mouse() {
    POINT p = {0, 0};
    GetCursorPos(&p);
    Coord c = {p.x, p.y};
    return c;
}

/* ----------------------------- 随机 ----------------------------- */
/* 自带 LCG, 不依赖 CRT 的 rand(), 因此不需要 #include <cstdlib> */
static unsigned long long g_rng = 88172645463393265ULL;

/* 命名为 ac_rand, 再用宏导出成 rand(...), 避免与 CRT 的 rand(void) 二义性 */
static int ac_rand(int seed = -1) {
    if (seed >= 0) g_rng = (unsigned long long)seed ^ GetTickCount64();
    g_rng = g_rng * 6364136223846793005ULL + 1442695040888963407ULL;
    return 10000 + (int)((g_rng >> 33) % 90001ULL);   /* 10^4 ~ 10^5 */
}

/* --------------------------- 输入模拟 --------------------------- */
static void send_mouse(DWORD down, DWORD up, Coord c, bool fixed, int hold_ms) {
    if (stopped()) return;
    POINT old = {0, 0};
    GetCursorPos(&old);
    if (fixed) SetCursorPos((int)c.x, (int)c.y);

    INPUT a = {0}, b = {0};
    a.type = INPUT_MOUSE; a.mi.dwFlags = down;
    b.type = INPUT_MOUSE; b.mi.dwFlags = up;
    SendInput(1, &a, sizeof(INPUT));
    if (hold_ms > 0) WaitForSingleObject(g_stopEvent, (DWORD)hold_ms);
    SendInput(1, &b, sizeof(INPUT));

    if (fixed) SetCursorPos(old.x, old.y);   /* 点击后恢复光标 */
}

static void left()                  { send_mouse(MOUSEEVENTF_LEFTDOWN,   MOUSEEVENTF_LEFTUP,   mouse(), false, 0); }
static void left(Coord c)           { send_mouse(MOUSEEVENTF_LEFTDOWN,   MOUSEEVENTF_LEFTUP,   c, true, 0); }
static void left(long x, long y)    { Coord c = {x, y}; left(c); }
static void right()                 { send_mouse(MOUSEEVENTF_RIGHTDOWN,  MOUSEEVENTF_RIGHTUP,  mouse(), false, 0); }
static void right(Coord c)          { send_mouse(MOUSEEVENTF_RIGHTDOWN,  MOUSEEVENTF_RIGHTUP,  c, true, 0); }
static void right(long x, long y)   { Coord c = {x, y}; right(c); }
static void middle()                { send_mouse(MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, mouse(), false, 0); }
static void middle(Coord c)         { send_mouse(MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, c, true, 0); }
static void middle(long x, long y)  { Coord c = {x, y}; middle(c); }

/* keyboard("Ctrl+Alt+K") —— 解析按键名, 返回一个 VK; 修饰键走 MOD_* */
static WORD vk_of(const char* s) {
    if (!s || !*s) return 0;
    if (ieq(s, "ENTER")) return VK_RETURN;
    if (ieq(s, "ESC") || ieq(s, "ESCAPE")) return VK_ESCAPE;
    if (ieq(s, "SPACE")) return VK_SPACE;
    if (ieq(s, "TAB")) return VK_TAB;
    if (ieq(s, "UP")) return VK_UP;
    if (ieq(s, "DOWN")) return VK_DOWN;
    if (ieq(s, "LEFT")) return VK_LEFT;
    if (ieq(s, "RIGHT")) return VK_RIGHT;
    if (s[0] == 'F' && s[1] >= '0' && s[1] <= '9') {
        int n = 0;
        for (const char* p = s + 1; *p >= '0' && *p <= '9'; ++p) n = n * 10 + (*p - '0');
        if (n >= 1 && n <= 12) return (WORD)(VK_F1 + n - 1);
    }
    if (s[1] == 0) {
        SHORT r = VkKeyScanA(s[0]);
        if (r != -1) return (WORD)(r & 0xFF);
    }
    return 0;
}

static void keyboard(const char* spec) {
    if (stopped() || !spec) return;
    char buf[128];
    snprintf(buf, sizeof(buf), "%s", spec);

    INPUT seq[8];
    int n = 0;
    char* save = NULL;
    char* tok = next_token(buf, "+", &save);
    while (tok && n < 7) {
        WORD vk = 0;
        if (ieq(tok, "CTRL") || ieq(tok, "CONTROL")) vk = VK_CONTROL;
        else if (ieq(tok, "ALT")) vk = VK_MENU;
        else if (ieq(tok, "SHIFT")) vk = VK_SHIFT;
        else if (ieq(tok, "WIN")) vk = VK_LWIN;
        else vk = vk_of(tok);
        if (vk) { ZeroMemory(&seq[n], sizeof(INPUT)); seq[n].type = INPUT_KEYBOARD; seq[n].ki.wVk = vk; n++; }
        tok = next_token(NULL, "+", &save);
    }
    if (!n) return;
    SendInput(n, seq, sizeof(INPUT));
    for (int i = n - 1; i >= 0; --i) {
        seq[i].ki.dwFlags = KEYEVENTF_KEYUP;
        SendInput(1, &seq[i], sizeof(INPUT));
    }
}

/* pause(*ms) —— 可被急停立即打断 */
static void pause(int ms) {
    if (ms <= 0) return;
    WaitForSingleObject(g_stopEvent, (DWORD)ms);
}

/* hotkey(*按键) —— 注册全局热键, 返回 hotkey id */
static int hotkey(const char* spec) {
    UINT mods = 0;
    WORD vk = 0;
    char buf[128];
    snprintf(buf, sizeof(buf), "%s", spec ? spec : "");
    char* save = NULL;
    char* tok = next_token(buf, "+", &save);
    while (tok) {
        if (ieq(tok, "CTRL") || ieq(tok, "CONTROL")) mods |= MOD_CONTROL;
        else if (ieq(tok, "ALT")) mods |= MOD_ALT;
        else if (ieq(tok, "SHIFT")) mods |= MOD_SHIFT;
        else if (ieq(tok, "WIN")) mods |= MOD_WIN;
        else vk = vk_of(tok);
        tok = next_token(NULL, "+", &save);
    }
    if (!vk) return 0;
    int id = 1;
    if (!RegisterHotKey(NULL, id, mods | MOD_NOREPEAT, vk)) return 0;
    return id;
}

/* =========================================================================
 * 用户代码区 —— 只改这里, 语法与程序内置的伪代码 DSL 一致
 * ========================================================================= */
#define rand ac_rand

static void user_code() {
    hotkey("Ctrl+Alt+X");                 /* 声明 / 注册启动热键 */

    int i = 0;
    int times = rand(2026);               /* 10^4 ~ 10^5 的随机数 */

    for (i = 0; i < 10; i++) {
        if (stopped()) break;
        Coord p = mouse();
        if (i % 2 == 0) {
            left(p);                      /* 跟随鼠标 */
        } else {
            left(320, 240);               /* 固定坐标 */
        }
        pause(100);                       /* n ms */
    }

    while (!stopped()) {
        keyboard("F5");
        pause(500);
    }
}

/* ========================================================================= */
static DWORD WINAPI worker(LPVOID) {
    user_code();
    return 0;
}

int WINAPI WinMain(HINSTANCE, HINSTANCE, LPSTR, int) {
    g_stopEvent = CreateEventW(NULL, TRUE, FALSE, NULL);
    if (!g_stopEvent) return 1;

    /* 常驻急停: Ctrl+Alt+F12 */
    RegisterHotKey(NULL, 0x0FFF, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_F12);

    HANDLE th = CreateThread(NULL, 0, worker, NULL, 0, NULL);

    MSG msg;
    while (GetMessageW(&msg, NULL, 0, 0) > 0) {
        if (msg.message == WM_HOTKEY && msg.wParam == 0x0FFF) {
            InterlockedExchange(&g_stopping, 1);
            SetEvent(g_stopEvent);
            break;
        }
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    if (th) {
        WaitForSingleObject(th, 3000);
        CloseHandle(th);
    }
    UnregisterHotKey(NULL, 0x0FFF);
    CloseHandle(g_stopEvent);
    return 0;
}
