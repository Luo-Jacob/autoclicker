# -*- coding: utf-8 -*-
"""从 studio.cpp 内嵌的 raw string 导出完整可编译模板 runtime_template.cpp。

工作台把「运行时头 + 用户代码 + 运行时尾」拼起来交给 g++ 真编译。
这个脚本保证仓库里的独立模板与工作台内嵌内容只有一份真源, 不会各自漂移。
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "studio.cpp")
DST = os.path.join(HERE, "runtime_template.cpp")


def grab(name):
    src = open(SRC, encoding="utf-8").read()
    m = re.search(r'static const char\*\s+' + name +
                  r'\s*=\s*R"ACPP\((.*?)\)ACPP";', src, re.S)
    if not m:
        raise SystemExit("找不到内嵌段落: " + name)
    return m.group(1)


def main():
    out = grab("RUNTIME_HEAD") + grab("DEFAULT_CODE") + grab("RUNTIME_TAIL")
    with open(DST, "w", encoding="utf-8", newline="\n") as f:
        f.write(out)
    print("已导出 %s (%d 字节)" % (DST, len(out.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
