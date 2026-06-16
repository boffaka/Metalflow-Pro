FROM node:20-alpine
WORKDIR /app
COPY public/index.html ./index.html
RUN npm install -g serve@14
EXPOSE 8080
CMD ["serve", "-l", "tcp://0.0.0.0:8080", "."]
