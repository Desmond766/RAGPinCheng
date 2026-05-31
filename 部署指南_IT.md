# 品成 BIM 知识库 — 内网部署指南（IT 人员）

服务已由开发人员在服务器上完成基本配置并启动。本文档说明如何将其暴露到公司内网，以及后续维护操作。

---

## 第一步：验证服务正常运行

```cmd
curl http://localhost/api/health
```

应返回：`{"status":"ok"}`

---

## 第二步：开放防火墙端口

**方式一：命令行（管理员 PowerShell）**

```powershell
netsh advfirewall firewall add rule name="品成知识库 Port 80" dir=in action=allow protocol=TCP localport=80
```

**方式二：图形界面**

控制面板 → Windows Defender 防火墙 → 高级设置 → 入站规则 → 新建规则 → 端口 → TCP → 特定端口填 `80` → 允许连接 → 完成。

---

## 第三步：确认内网 IP 并通知员工

```cmd
ipconfig
```

找到 `以太网适配器` 下的 `IPv4 地址`，例如 `192.168.1.100`。

通知员工在浏览器访问：

```
http://192.168.1.100
```

首次访问可在 `/register` 页面自助注册账号，或由管理员在后台创建。

---

## 日常维护

**查看服务状态：**
```cmd
docker compose -f docker/docker-compose.yml ps
```

**重启服务：**
```cmd
docker compose -f docker/docker-compose.yml restart
```

**停止服务：**
```cmd
docker compose -f docker/docker-compose.yml down
```

**更新代码后重新部署：**
```cmd
git pull
docker compose -f docker/docker-compose.yml build
docker compose -f docker/docker-compose.yml up -d
```

**查看日志排查问题：**
```cmd
docker compose -f docker/docker-compose.yml logs -f backend
```

---

## 上传资料到知识库

### 方式一：管理后台上传（推荐，日常使用）

使用管理员账号在浏览器操作，无需登录服务器：

1. 访问 `http://<服务器IP>/admin`
2. 进入「资料管理」标签
3. 选择分类，上传 PDF 或 Markdown 文件
4. 等待进度条显示「完成」

### 方式二：批量导入（首次大批量上传时使用）

**第一步：把文件放到 `docs/` 目录下对应分类文件夹**

```
C:\RAGPinCheng\docs\
  行业规范\        ← 行业标准、规范类 PDF
  客户标准\
    <客户名>\      ← 客户标准必须有二级目录，文件夹名为客户名
  公司内部标准\    ← 公司内部标准 PDF
  项目资料\        ← 项目交付物、复盘资料
  教学视频\        ← 培训视频转写稿（.md 格式）
```

**第二步：在容器内运行索引脚本**

```cmd
cd C:\RAGPinCheng
docker compose -f docker/docker-compose.yml exec backend python scripts/build_index.py
```

脚本是**增量的**，只处理新增文件，不会重复处理已有内容。索引过程中终端会显示每个文件的解析和向量化进度，全部完成后退出。

> 注意：索引过程需要调用 MinerU API 解析 PDF，每个 PDF 约需 1 分钟，请确保服务器能访问外网。

---

## 可提升项

以下内容非部署必须，但能提升安全性和易用性，条件允许时建议实施。

### 1. 内网域名（更易记的访问地址）

让员工通过 `http://bim-kb` 或 `http://knowledge.pincheng.local` 访问，而不是记 IP。

**方式一：修改每台员工电脑的 hosts 文件**（适合人少的团队）

在每台员工电脑上，用管理员权限编辑 `C:\Windows\System32\drivers\etc\hosts`，追加一行：

```
192.168.1.100  bim-kb
```

之后员工访问 `http://bim-kb` 即可。

**方式二：公司内网 DNS**（适合有 Windows Server / 路由器 DNS 的公司）

在公司 DNS 服务器上添加一条 A 记录，将域名指向服务器 IP。员工无需任何设置，全公司立即生效。

---

### 2. HTTPS 加密（防止内网明文传输）

当前 HTTP 部署下，员工登录时账号密码在内网以明文传输。如果公司有 WiFi 环境或对数据安全有要求，建议加一层反向代理。

**推荐工具：Caddy**（配置最简单）

1. 下载 [Caddy for Windows](https://caddyserver.com/download)，放到服务器任意目录。

2. 在同目录创建 `Caddyfile`：

```
# 用 IP 访问（自签名证书，浏览器会有一次警告）
https://192.168.1.100 {
    tls internal
    reverse_proxy localhost:80
}

# 或者配合内网域名使用
https://bim-kb {
    tls internal
    reverse_proxy localhost:80
}
```

3. 以管理员身份运行：

```cmd
caddy run
```

4. 在 `C:\RAGPinCheng\.env` 里删除 `SESSION_COOKIE_SECURE=false` 这一行（或改为 `true`），然后重启容器：

```cmd
docker compose -f docker/docker-compose.yml restart
```

5. 员工首次访问时浏览器会提示"证书不受信任"（自签名），点击「高级」→「继续访问」即可，之后不再提示。

> 如果公司有自己的内部 CA 证书，可以用它签发证书替换 `tls internal`，员工就不会看到警告了。

---

## 常见问题

**Q：浏览器能访问 localhost 但别人访问不了？**
A：检查防火墙是否放行了 80 端口（见第二步）。

**Q：服务器重启后服务没自动启动？**
A：Docker Desktop 设置里勾选「Start Docker Desktop when you log in」，且配置了 `restart: unless-stopped`，Docker 服务启动后容器会自动恢复。

**Q：登录后一直跳回登录页？**
A：确认 `C:\RAGPinCheng\.env` 里有 `SESSION_COOKIE_SECURE=false`（未启用 HTTPS 时必须）。
