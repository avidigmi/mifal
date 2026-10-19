"""Local invocation policy; source flags describe eligibility, not runtime discovery."""

import json
import re


def read_policy(path):
    policy = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(policy, dict) or set(policy) - {"default", "model_invocation_allowlist"}:
        raise ValueError("Unsupported invocation policy schema")
    if "default" in policy and policy["default"] != "auto":
        raise ValueError("Invocation policy default must be auto")
    names = policy.get("model_invocation_allowlist", [])
    if not isinstance(names, list) or any(not isinstance(n, str) for n in names):
        raise ValueError("Invocation allowlist must contain skill names")
    return policy


def automatic(policy, name):
    return policy.get("default") == "auto" or name in policy.get("model_invocation_allowlist", [])


def controlled_lines(content, relative):
    lines = content.decode().splitlines(keepends=True)
    candidates = []
    if relative == "SKILL.md":
        if not lines or lines[0].strip() != "---":
            raise ValueError("Missing skill frontmatter")
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is None:
            raise ValueError("Unclosed skill frontmatter")
        first = next((line for line in lines[1:end] if line.strip() and not line.lstrip().startswith("#")), "")
        if first and first[0].isspace():
            raise ValueError("Unsupported frontmatter indentation; manual review required")
        key = "disable-model-invocation"
        candidates = [i for i in range(1, end) if re.match(rf"^(?:{key}|['\"]{key}['\"])\s*:", lines[i])]
    elif relative == "agents/openai.yaml":
        key = "allow_implicit_invocation"
        in_policy = False
        policy_blocks = 0
        child_indent = None
        for i, line in enumerate(lines):
            if re.match(r"^[ \t]+(?:policy|['\"]policy['\"])\s*:", line):
                raise ValueError("Unsupported Codex policy indentation; manual review required")
            if re.match(r"^[ \t]*<<\s*:", line) or line.lstrip().startswith(("{", "[")):
                raise ValueError("Unsupported Codex mapping structure; manual review required")
            if re.match(r"^(?:policy|['\"]policy['\"])\s*:", line):
                policy_blocks += 1
                if policy_blocks > 1:
                    raise ValueError("Duplicate Codex policy block")
                if not re.fullmatch(r"policy\s*:\s*(?:#.*)?\r?\n?", line):
                    raise ValueError("Unsupported inline Codex policy; manual review required")
                in_policy = True
                child_indent = None
            elif line.strip() and not line.lstrip().startswith("#") and not line[0].isspace():
                in_policy = False
            elif in_policy and line.strip() and not line.lstrip().startswith("#"):
                leading = line[:len(line) - len(line.lstrip())]
                if "\t" in leading:
                    raise ValueError("Unsupported Codex policy indentation")
                if child_indent is None:
                    child_indent = len(leading)
                if len(leading) < child_indent:
                    raise ValueError("Unsupported Codex policy indentation")
                if len(leading) == child_indent and re.match(rf"^[ ]+(?:{key}|['\"]{key}['\"])\s*:", line):
                    candidates.append(i)
    else:
        return lines, []
    if len(candidates) > 1:
        raise ValueError(f"Duplicate invocation flag: {key}")
    parsed = []
    for i in candidates:
        match = re.fullmatch(rf"([ \t]*{key}\s*:[ \t]*)(true|false)([ \t]*(?:#[^\r\n]*)?)(\r?\n)?", lines[i], re.IGNORECASE)
        if not match:
            raise ValueError(f"Unsupported invocation flag: {lines[i].strip()}")
        parsed.append((i, match))
    return lines, parsed


def declared(content, relative):
    _, flags = controlled_lines(content, relative)
    return flags[0][1][2].lower() == "true" if flags else None


def normalize(content, relative):
    if relative not in ("SKILL.md", "agents/openai.yaml"):
        return content
    lines, flags = controlled_lines(content, relative)
    for i, match in flags:
        if relative == "SKILL.md":
            lines[i] = ""
        elif match[2].lower() == "false":
            lines[i] = match[1] + "true" + match[3] + (match[4] or "")
    return "".join(lines).encode()
