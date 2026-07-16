aws ecr get-login-password --region ap-southeast-1 \
  | docker login --username AWS --password-stdin 169615917687.dkr.ecr.ap-southeast-1.amazonaws.com

docker buildx build \
  --platform linux/amd64 \
  --provenance=false \
  -f Dockerfile.lambda \
  -t 169615917687.dkr.ecr.ap-southeast-1.amazonaws.com/entourage-blog-publisher:latest_v34 \
  --push \
  .
