import re


def encode_cwd(cwd: str) -> str:
    """Encode a working directory path to the Claude Code transcript directory name.

    Rules (matching Claude Code's own encoder):
    - On Windows paths (second char is ':'): lowercase the drive letter.
    - Replace each of ':', '\\', '/', '.', ' ' with '-'.
    """
    path = cwd
    if len(path) >= 2 and path[1] == ":":
        path = path[0].lower() + path[1:]
    return re.sub(r"[:\\/. ]", "-", path)
