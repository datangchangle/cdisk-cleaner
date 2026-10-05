# 免 Python 环境运行（CI 自动构建 exe）
本仓库只有一个作用：用 GitHub Actions 在 Windows 云端环境把 `cleaner_main.py` 打包成 exe，并自动发布到 Releases。

- 推送到 main 分支即自动构建
- 构建完成后在仓库 Releases 页面出现 `C盘清理大师.exe`
- 也可在 Actions 页面手动触发（workflow_dispatch）
