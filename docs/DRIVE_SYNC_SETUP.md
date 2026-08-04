# 自动同步文档到 Google Drive —— 一次性配置

配好后:**每次 push 改了 `docs/`,GitHub Action 会自动把 `docs/` 镜像到你 Drive 文件夹里的 `stock-agent-docs` 子文件夹。** 全程无需再手动上传。

> 为什么需要你亲自做一步:访问你的 Google Drive 必须由**你本人授权**(OAuth)。任何人(包括我)都无法替你登录你的 Google 账号——这是安全设计,不是限制。做完这一步,后续全自动。

管线(`.github/workflows/sync-docs-to-drive.yml`)已就位。你只需完成下面三步。

---

## 步骤 1:本地用 rclone 拿到你的 Drive 授权 token

在你自己的电脑上(需要浏览器):

```bash
# 安装 rclone(mac: brew install rclone;linux: curl https://rclone.org/install.sh | sudo bash)
rclone config
```

交互里依次:
1. `n`(新建 remote)
2. name 填:`gdrive`
3. Storage 选 `drive`(Google Drive)
4. `client_id` / `client_secret` 直接回车留空(用 rclone 内置的即可,个人用足够)
5. scope 选 `1`(Full access,`drive`)
6. `root_folder_id` / `service_account_file` 回车留空
7. `Edit advanced config?` → `n`
8. `Use auto config?` → `y`(会打开浏览器让你登录 Google 并授权)
9. `Configure this as a Shared Drive?` → `n`
10. 确认保存 `y`,然后 `q` 退出

现在看看生成的配置文件内容(**下一步要用**):

```bash
cat "$(rclone config file | tail -1)"
```

会输出类似:
```
[gdrive]
type = drive
scope = drive
token = {"access_token":"...","refresh_token":"...","expiry":"..."}
team_drive =
```

---

## 步骤 2:把配置放进 GitHub Secret + 文件夹 ID 放进 Variable

在仓库 `EchoRaven/stock-agent` 的 **Settings → Secrets and variables → Actions**:

**Secret**(New repository secret):
- Name:`RCLONE_CONF`
- Value:粘贴步骤 1 里 `cat` 出来的**整段** rclone.conf 内容(从 `[gdrive]` 到最后)

**Variable**(Variables 标签页 → New repository variable):
- Name:`GDRIVE_FOLDER_ID`
- Value:`19Gec4Eff_y8nAFts_CjgPQzl4FuI0M5_`
  （就是你分享链接 `drive.google.com/drive/folders/<这一段>` 里的 ID;换文件夹改这里即可）

---

## 步骤 3:触发一次

- 到仓库 **Actions → "Sync docs to Google Drive" → Run workflow** 手动跑一次;
- 或随便 push 一个 `docs/` 的改动。

成功后,你的 Drive 文件夹里会出现 `stock-agent-docs/`,里面是全部 `.md` 文档。之后每次 push 自动更新。

---

## 说明 / 常见问题

- **安全**:`sync` 会让目标与 `docs/` 完全一致(删掉多余文件),但**限定在 `stock-agent-docs` 子文件夹内**,绝不动你 Drive 文件夹里的其它文件。
- **想要 Google 文档格式而非 .md**:同步上去的是 `.md` 文件(Drive 可预览)。若要可协作编辑的 Google Doc,在 Drive 里右键该文件「用 Google 文档打开」即可转换;自动转换需额外脚本,按需再加。
- **token 泄露风险**:`RCLONE_CONF` 里的 refresh_token 等同你 Drive 的写权限,放在 GitHub Secret(加密、不出现在日志)。若担心,可在步骤 1 第 5 步把 scope 选 `drive.file`(只让 rclone 访问它自己创建的文件),权限最小化。
- **未配置时**:workflow 有 `if: vars.GDRIVE_FOLDER_ID != ''` 守卫,没配就直接跳过,不会报红、不影响仓库。
- **替代方案**:若你不想用 GitHub Action,也可在服务器上 `rclone config` 后加一个 cron/git-hook 跑 `rclone sync docs gdrive:stock-agent-docs`——同理需先完成步骤 1 的授权。
