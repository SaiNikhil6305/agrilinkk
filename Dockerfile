FROM node:20-slim AS web
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
COPY ml_backend/requirements.txt ml_backend/requirements.txt
RUN pip install --no-cache-dir -r ml_backend/requirements.txt
COPY ml_backend ml_backend
COPY crop_samples crop_samples
COPY --from=web /app/dist dist
RUN mkdir -p crop_quality_results/Premium crop_quality_results/Good crop_quality_results/Bad
ENV PORT=5000 HOST=0.0.0.0
EXPOSE 5000
WORKDIR /app/ml_backend
CMD ["python", "server.py"]
