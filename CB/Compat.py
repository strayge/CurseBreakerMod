# pyright: reportPossiblyUnboundVariable=false
import os
import sys
from typing import Any

IS_WINDOWS = sys.platform == 'win32'

if IS_WINDOWS:
    import msvcrt
else:
    import termios
    import atexit
    from select import select


class KBHit:
    def __init__(self) -> None:
        if IS_WINDOWS:
            return
        else:
            self.fd: int = sys.stdin.fileno()
            self.new_term: list[Any] = termios.tcgetattr(self.fd)
            self.old_term: list[Any] = termios.tcgetattr(self.fd)
            self.new_term[3] = (self.new_term[3] & ~termios.ICANON & ~termios.ECHO)
            termios.tcsetattr(self.fd, termios.TCSAFLUSH, self.new_term)
            atexit.register(self.set_normal_term)

    def set_normal_term(self) -> None:
        if IS_WINDOWS:
            return
        else:
            termios.tcsetattr(self.fd, termios.TCSAFLUSH, self.old_term)

    def getch(self) -> str | bytes:
        if IS_WINDOWS:
            return msvcrt.getch()
        else:
            return sys.stdin.read(1)

    def kbhit(self) -> bool:
        if IS_WINDOWS:
            return msvcrt.kbhit()
        else:
            dr, _, _ = select([sys.stdin], [], [], 0)
            return dr != []


def clear() -> None:
    if IS_WINDOWS:
        os.system('cls')
    else:
        print('\033c', end='')


def set_terminal_title(title: str) -> None:
    if IS_WINDOWS:
        os.system(f'title {title}')
    else:
        os.system(f'echo "\033]0;{title}\007"')


def set_terminal_size(w: int, h: int) -> None:
    if IS_WINDOWS:
        os.system(f'mode con: cols={w} lines={h}')
    else:
        os.system(f'printf \'\033[8;{h};{w}t\'')
