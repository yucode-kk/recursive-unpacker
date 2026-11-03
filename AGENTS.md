# AGENTS.md

本文件面向在本仓库工作的 AI 代理与协作者，记录**必须遵守**的硬性约束。

## 一、提交安全：不得泄露密钥

**提交前必须确认暂存区不含任何密钥、口令、令牌或凭据。这是强制规则，优先于任何「方便调试」的诉求。**

### 禁止提交的内容

- 密码、解析出的候选密码列表
- API Key、Access Token、Secret、Private Key（`*.pem`、`*.key`、`*.pfx`）
- 数据库连接串、带凭据的 URL（`scheme://user:pass@host`）
- 会话 Cookie、SSH 私钥、云厂商凭据文件
- `.env`、`config.json` 等本机运行配置

### 本仓库的具体要求

1. `config.json` 与 `config.json.bak` 保存**明文解压密码**，必须保持在 `.gitignore` 中。新增任何含凭据的生成文件时，**先加 `.gitignore` 再写文件**。
2. 代码与测试中只允许出现明显的假值（如 `"wrong"`、`"secret"` 这类用于断言密码轮询行为的测试常量）；真实密码一律不写入仓库。
3. 日志、异常消息、`dataclass` 的 `repr`、`messagebox` 文案中**不得拼接密码**。需要提示时应表述为「最近一次成功的密码（不落盘）」之类的描述性文字。
4. 提交前自查：
   ```bash
   git diff --cached --name-only
   git diff --cached | grep -nEi "(password|passwd|secret|token|api[_-]?key|BEGIN [A-Z ]*PRIVATE KEY)"
   ```
   命中即逐条确认是否为无害的测试假值；确认为真实凭据时立即 `git restore --staged <file>`，不要提交。
5. **一旦密钥已进入提交历史，仅删除文件不足以补救**。应立即停止推送，告知用户撤销并轮换该凭据；不得自行改写历史或强制推送。

## 二、项目既有全局约束

来自 `README.md` 与 `docs/superpowers/`，改动代码时不得违反：

1. **永不修改或删除用户的原始输入文件。**
2. **永不把密码写入日志或异常消息。** 密码可按用户知情以明文保存于本机 `config.json`（已被 gitignore 排除）。
3. **拒绝可能逃逸解压根目录的压缩包成员路径。**
4. 仅在解压成功后、且用户开启该选项时，才删除内层压缩包。分卷压缩包（`.7z.001`）必须整组删除，只删 `.001` 会留下无法再用的残片。
5. 达到递归层数上限或遇到不支持的格式时，**必须如实报告为「未完全展开」**，不得声称成功完成。
6. **分卷压缩包必须以原始路径交给外部程序**（Bandizip / 7-Zip）；复制成 `candidate.7z` 或改名都会切断卷链。纯 Python 后端不支持分卷，缺外部程序时应给出明确提示而非含糊失败。

## 三、开发约定

- 提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/)（`feat:` / `fix:` / `test:` / `docs:` / `refactor:`）。
- 改动后运行：
  ```bash
  py -3 -m pytest -q
  py -3 -m compileall -q main.py recursive_unpacker tests
  ```
- 涉及解压或安全逻辑的改动，必须补充对应测试用例。
- 测试不得依赖本机安装的 Bandizip / 7-Zip：走 `extract_recursive` 的用例需 mock `find_bandizip_executable` / `find_7zip_executable` 或 `subprocess.run`。
- 代码小步、可测试；单文件以 200–400 行为宜，上限 800 行。
