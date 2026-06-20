# 🚀 Adjust 看板部署指引（Render.com 免费部署）

## 📁 部署文件清单（deploy 文件夹里的5个文件）
```
deploy/
  ├── app.py              ← 后端服务
  ├── channel.html        ← Channel 看板页面
  ├── campaign.html       ← Campaign 看板页面
  ├── requirements.txt    ← 依赖
  └── Procfile            ← 启动命令
```

---

## 步骤1：上传到 GitHub（5分钟）

1. 打开 https://github.com，登录或注册
2. 点右上角 **+** → **New repository**
3. 仓库名随意，如 `adjust-dashboard`，选 **Public**，点 **Create repository**
4. 点 **uploading an existing file**
5. 把 `deploy/` 文件夹里的 **5个文件** 全部拖进去
6. 点 **Commit changes**

---

## 步骤2：部署到 Render（5分钟）

1. 打开 https://render.com，用 GitHub 账号登录
2. 点 **New +** → **Web Service**
3. 选择刚才创建的 GitHub 仓库
4. 配置如下：

| 字段 | 填写内容 |
|------|---------|
| Name | adjust-dashboard（随意） |
| Runtime | **Python 3** |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn app:app --bind 0.0.0.0:$PORT --timeout 120` |

5. 点 **Create Web Service**，等待约 2 分钟部署完成
6. 得到链接，如：`https://adjust-dashboard.onrender.com`

---

## 访问地址

| 页面 | URL |
|------|-----|
| Channel 看板 | `https://你的链接.onrender.com/` |
| Campaign 看板 | `https://你的链接.onrender.com/campaign` |

---

## ⚠️ 注意事项

- **免费版冷启动**：15分钟无访问后会休眠，首次访问需等待约 30 秒唤醒
- **数据实时**：每次点日期按钮都会实时调用 Adjust API，数据永远最新
- **🔄 刷新按钮**：点击可手动重新拉取最新数据
