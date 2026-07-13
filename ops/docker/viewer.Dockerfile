FROM node:22-alpine AS build
WORKDIR /viewer
COPY web/viewer/package.json web/viewer/package-lock.json ./
RUN npm ci
COPY web/viewer/ ./
ARG VITE_ROADGEN_DEFAULT_ROUTE=course-studio
ENV VITE_ROADGEN_DEFAULT_ROUTE=$VITE_ROADGEN_DEFAULT_ROUTE
RUN npm run build

FROM nginx:1.27-alpine
COPY ops/docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /viewer/dist /usr/share/nginx/html
EXPOSE 80
