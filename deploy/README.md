# 远程 Linux 部署指南（nginx 反代 + uvicorn）

## 前提

- Linux 服务器已装 Python 3.10+、nginx、Node.js 18+（仅构建前端用）
- 8000 端口已被其他服务占用，nginx 已在运行

## 第一步：改端口

```bash
cd 熔岩管展示/backend
# 改 config.py 里的 PORT 为一个空闲端口（比如 5100）
sed -i 's/PORT = 5000/PORT = 5100/' sim/config.py
# 同时改 HOST 为 127.0.0.1（保持不变，只让 nginx 能访问）
```

## 第二步：安装依赖 + 构建前端

```bash
# 后端依赖
pip install -r requirements.txt

# 前端构建（如果 dist 已有可跳过）
cd ../frontend
npm install && npm run build
```

## 第三步：启动服务

```bash
cd ../backend
nohup python main.py > server.log 2>&1 &
# 验证
curl http://127.0.0.1:5100/health
```

## 第四步：nginx 配置

在 `/etc/nginx/sites-available/` 或 `/etc/nginx/conf.d/` 新建 `lava-tube.conf`：

```nginx
# 方案A: 独立子域名/独立端口（推荐）
server {
    listen 8080;                          # 对外的端口，选一个空闲的
    server_name _;

    # 前端静态文件 + API + WebSocket 全部反代到 uvicorn
    location / {
        proxy_pass http://127.0.0.1:5100;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket 超时（沙盘长连接）
        proxy_read_timeout 86400;
        proxy_send_timeout 86400;
    }

    # 静态资源缓存加速
    location /assets/ {
        proxy_pass http://127.0.0.1:5100/assets/;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }
}
```

```bash
# 启用
sudo ln -s /etc/nginx/sites-available/lava-tube.conf /etc/nginx/sites-enabled/
# 或者 conf.d 方式直接放进去即可

# 测试 + 重载
sudo nginx -t && sudo nginx -s reload
```

## 方案B：挂在已有域名的子路径下

如果你不想额外占端口，也可以挂在已有 nginx 站点的子路径：

```nginx
# 加到你已有的 server { listen 8000; ... } 里面
location /lava/ {
    proxy_pass http://127.0.0.1:5100/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_read_timeout 86400;
}
```

> 注意：子路径方式需要前端也配 `base: '/lava/'`，比较麻烦，推荐方案A。

## 验证

```bash
# 服务器上
curl http://127.0.0.1:5100/health          # 直连后端
curl http://127.0.0.1:8080/health           # 经 nginx

# 浏览器打开
http://你的服务器IP:8080/
```

## 常见问题

| 问题 | 原因 | 解决 |
|---|---|---|
| WebSocket 连不上 | nginx 没配 Upgrade 头 | 确认 `proxy_set_header Upgrade` 和 `Connection "upgrade"` 两行在 |
| 页面白屏 | dist 没构建 | `cd frontend && npm run build` |
| 502 Bad Gateway | uvicorn 没启动 | `curl 127.0.0.1:5100/health` 检查，看 `server.log` |
| WS 频繁断线 | nginx 超时太短 | 确认 `proxy_read_timeout 86400` |
