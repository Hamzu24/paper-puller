#!/usr/bin/env python3
"""
Simple TUI for browsing and opening papers from papers_* directories.
"""

import os
import subprocess
import sys
import tty
import termios


# ANSI escape codes
class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    CYAN = '\033[36m'
    YELLOW = '\033[33m'
    GREEN = '\033[32m'
    REVERSE = '\033[7m'
    CLEAR_SCREEN = '\033[2J'
    CURSOR_HOME = '\033[H'
    HIDE_CURSOR = '\033[?25l'
    SHOW_CURSOR = '\033[?25h'
    CLEAR_LINE = '\033[K'


def get_terminal_size():
    try:
        size = os.get_terminal_size()
        return size.lines, size.columns
    except:
        return 24, 80


def get_folders(base_dir='.'):
    """Get list of papers_* directories."""
    folders = []
    for entry in sorted(os.listdir(base_dir)):
        path = os.path.join(base_dir, entry)
        if os.path.isdir(path) and entry.startswith('papers'):
            # Count PDFs in this folder
            pdf_count = sum(1 for f in os.listdir(path) if f.lower().endswith('.pdf'))
            display = entry.replace('papers_', '').replace('_', ' ')
            if entry == 'papers':
                display = 'papers (default)'
            folders.append({
                'name': entry,
                'path': path,
                'display': display,
                'count': pdf_count,
            })
    return folders


def get_papers(directory='papers'):
    """Get list of PDF files in the directory."""
    if not os.path.exists(directory):
        return []

    papers = []
    for f in sorted(os.listdir(directory)):
        if f.lower().endswith('.pdf'):
            path = os.path.join(directory, f)
            size = os.path.getsize(path)
            if size > 1024 * 1024:
                size_str = f"{size / (1024*1024):.1f} MB"
            else:
                size_str = f"{size / 1024:.0f} KB"
            papers.append({
                'name': f,
                'path': path,
                'size': size_str,
                'display': f.replace('.pdf', '').replace('-', ' ')
            })
    return papers


def getch():
    """Read a single character from stdin."""
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(sys.stdin.fileno())
        ch = sys.stdin.read(1)
        # Handle arrow keys (escape sequences)
        if ch == '\x1b':
            ch2 = sys.stdin.read(1)
            if ch2 == '[':
                ch3 = sys.stdin.read(1)
                if ch3 == 'A':
                    return 'UP'
                elif ch3 == 'B':
                    return 'DOWN'
                elif ch3 == '5':
                    sys.stdin.read(1)  # consume ~
                    return 'PGUP'
                elif ch3 == '6':
                    sys.stdin.read(1)  # consume ~
                    return 'PGDN'
            return 'ESC'
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def truncate(text, width):
    """Truncate text to fit width."""
    if len(text) <= width:
        return text
    return text[:width-3] + '...'


def folder_screen():
    """Show folder selection screen. Returns selected folder path or None to quit."""
    folders = get_folders()
    if not folders:
        print("No papers folders found.")
        return None

    # Skip folder selection if there's only one folder
    if len(folders) == 1:
        return folders[0]['path']

    selected = 0

    while True:
        height, width = get_terminal_size()
        list_height = height - 5

        scroll_offset = max(0, selected - list_height + 1)
        if selected < scroll_offset:
            scroll_offset = selected

        sys.stdout.write(Colors.CLEAR_SCREEN + Colors.CURSOR_HOME)

        # Header
        print(f"{Colors.CYAN}{Colors.BOLD} Paper Folders ({len(folders)}) {Colors.RESET}")
        print(f" {Colors.DIM}q quit  Enter open{Colors.RESET}")

        # Separator
        print("\u2500" * (width - 1))

        # Folder list
        visible_start = scroll_offset
        visible_end = min(scroll_offset + list_height, len(folders))

        for folder_idx in range(visible_start, visible_end):
            folder = folders[folder_idx]

            count_str = f"{folder['count']} papers"
            count_width = 14
            name_width = width - count_width - 6
            display_name = truncate(folder['display'], name_width)

            if folder_idx == selected:
                print(f"{Colors.REVERSE} > {display_name.ljust(name_width)} {count_str.rjust(12)} {Colors.RESET}")
            else:
                print(f"   {display_name.ljust(name_width)} {Colors.YELLOW}{count_str.rjust(12)}{Colors.RESET}")

        # Pad remaining lines
        lines_printed = 3 + min(list_height, len(folders))
        for _ in range(height - lines_printed - 1):
            print()

        # Footer
        current = folders[selected]
        print(f" {Colors.DIM}[{selected + 1}/{len(folders)}] {current['name']}/{Colors.RESET}")

        sys.stdout.flush()

        # Handle input
        key = getch()

        if key == 'q':
            return None
        elif key == 'UP' or key == 'k':
            if selected > 0:
                selected -= 1
        elif key == 'DOWN' or key == 'j':
            if selected < len(folders) - 1:
                selected += 1
        elif key == 'g':
            selected = 0
        elif key == 'G':
            selected = len(folders) - 1
        elif key == '\r' or key == '\n':
            return folders[selected]['path']


def paper_screen(directory):
    """Show paper list for a directory. Returns 'back' to go back, None to quit."""
    all_papers = get_papers(directory)
    folder_name = os.path.basename(directory)

    selected = 0
    search_query = ""
    search_mode = False

    while True:
        # Filter papers
        if search_query:
            papers = [p for p in all_papers if search_query.lower() in p['display'].lower()]
        else:
            papers = all_papers

        # Clamp selected
        if papers:
            selected = max(0, min(selected, len(papers) - 1))
        else:
            selected = 0

        # Render
        height, width = get_terminal_size()
        list_height = height - 5

        # Calculate scroll offset
        scroll_offset = max(0, selected - list_height + 1)
        if selected < scroll_offset:
            scroll_offset = selected

        sys.stdout.write(Colors.CLEAR_SCREEN + Colors.CURSOR_HOME)

        # Header
        print(f"{Colors.CYAN}{Colors.BOLD} {folder_name} ({len(papers)}) {Colors.RESET}")

        # Search bar
        if search_mode:
            print(f" Search: {search_query}_")
        elif search_query:
            print(f" Filter: {search_query} {Colors.DIM}(/ to edit, Esc clear){Colors.RESET}")
        else:
            print(f" {Colors.DIM}/ search  b back  q quit  Enter open{Colors.RESET}")

        # Separator
        print("\u2500" * (width - 1))

        # Paper list
        if not papers:
            if all_papers:
                print(f" {Colors.DIM}No papers match filter{Colors.RESET}")
            else:
                print(f" {Colors.DIM}No papers in this folder{Colors.RESET}")
        else:
            visible_start = scroll_offset
            visible_end = min(scroll_offset + list_height, len(papers))

            for display_row, paper_idx in enumerate(range(visible_start, visible_end)):
                paper = papers[paper_idx]

                # Calculate widths
                size_width = 10
                name_width = width - size_width - 6
                display_name = truncate(paper['display'], name_width)

                if paper_idx == selected:
                    # Highlighted row
                    print(f"{Colors.REVERSE} > {display_name.ljust(name_width)} {paper['size'].rjust(8)} {Colors.RESET}")
                else:
                    print(f"   {display_name.ljust(name_width)} {Colors.YELLOW}{paper['size'].rjust(8)}{Colors.RESET}")

        # Pad remaining lines
        lines_printed = 3 + (min(list_height, len(papers)) if papers else 1)
        for _ in range(height - lines_printed - 1):
            print()

        # Footer with debug info
        if papers:
            current_paper = papers[selected]
            print(f" {Colors.DIM}[{selected + 1}/{len(papers)}] Will open: {current_paper['name'][:50]}{Colors.RESET}")

        sys.stdout.flush()

        # Handle input
        key = getch()

        if search_mode:
            if key == 'ESC' or key == '\x1b':
                search_mode = False
                search_query = ""
                selected = 0
            elif key == '\r' or key == '\n':
                search_mode = False
            elif key == '\x7f' or key == '\x08':  # Backspace
                search_query = search_query[:-1]
                selected = 0
            elif key.isprintable():
                search_query += key
                selected = 0
        else:
            if key == 'q':
                return None
            elif key == 'b' or key == 'ESC':
                return 'back'
            elif key == '/':
                search_mode = True
            elif key == 'UP' or key == 'k':
                if selected > 0:
                    selected -= 1
            elif key == 'DOWN' or key == 'j':
                if papers and selected < len(papers) - 1:
                    selected += 1
            elif key == 'PGUP':
                selected = max(0, selected - list_height)
            elif key == 'PGDN':
                if papers:
                    selected = min(len(papers) - 1, selected + list_height)
            elif key == 'g':
                selected = 0
            elif key == 'G':
                if papers:
                    selected = len(papers) - 1
            elif (key == '\r' or key == '\n') and papers:
                paper = papers[selected]
                subprocess.Popen(
                    ['evince', paper['path']],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )


def main():
    print(Colors.HIDE_CURSOR, end='')

    try:
        while True:
            folder_path = folder_screen()
            if folder_path is None:
                break

            result = paper_screen(folder_path)
            if result is None:
                break
            # result == 'back' -> loop back to folder screen

    finally:
        print(Colors.SHOW_CURSOR + Colors.CLEAR_SCREEN + Colors.CURSOR_HOME, end='')
        sys.stdout.flush()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print(Colors.SHOW_CURSOR, end='')
