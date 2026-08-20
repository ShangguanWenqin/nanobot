"""
Entry point for running nanobot as a module: python -m nanobot
"""

from nanobot.cli.entry import main

# 模块执行只转交给 CLI 组合根，不在此处重复装配运行时对象。
if __name__ == "__main__":
    main()
