FROM node:20-alpine
WORKDIR /app
# Copy source: works whether build context is project root or frontend/ directory
COPY . .
RUN if [ -f "public/index.html" ]; then \
      cp public/index.html index.html; \
    elif [ -f "frontend/public/index.html" ]; then \
      cp frontend/public/index.html index.html; \
    fi
RUN npm install -g serve@14
EXPOSE 8080
CMD ["serve", "-l", "tcp://0.0.0.0:8080", "."]
