# TLS для integration API (nginx)

Production: terminate TLS на nginx перед frontend/backend.

```nginx
server {
    listen 443 ssl http2;
    server_name eskd.example.local;

    ssl_certificate     /etc/nginx/certs/eskd.crt;
    ssl_certificate_key /etc/nginx/certs/eskd.key;
    ssl_protocols       TLSv1.2 TLSv1.3;

    client_max_body_size 200m;

    location /api/ {
        proxy_pass http://backend:8080/api/;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Host $host;
    }

    location / {
        try_files $uri $uri/ /index.html;
        root /usr/share/nginx/html;
    }
}
```

Секреты — только через env/vault, не в репозитории.
