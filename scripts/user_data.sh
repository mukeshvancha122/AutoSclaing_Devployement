# runs on EC2 on boot

exec > >(tee /var/log/user-data.log|logger -t user-data -s 2>/dev/console) 2>&1
echo "{\"ts\":\"$(date -Is)\",\"stage\":\"start\",\"message\":\"user-data started\"}"

# variables

BUCKET=saimukeshreddyvanchas3
REGION=us-east-1
BUCKET_PREFIX="s3://$BUCKET/webroot"

echo "{\"ts\":\"$(date -Is)\",\"stage\":\"vars\",\"bucket\":\"$BUCKET_NAME\",\"prefix\":\"$BUCKET_PREFIX\",\"region\":\"$REGION\"}"

# install packages
dnf update -y
dnf install -y httpd awscli

# start apache automatically on boot 
systemctl enable httpd 
systemctl start httpd 

# sync the site content from s3 bucket 
ROOT_DIR="/var/www/html"
mkdir -p $ROOT_DIR
aws s3 sync "s3://$BUCKET/webroot" "$ROOT_DIR" --region "$REGION" --delete

chown -R apache:apache "$ROOT_DIR"
chmod -R 0755 "$ROOT_DIR"

systemctl restart httpd

echo "{\"ts\":\"$(date -Is)\",\"stage\":\"complete\",\"message\":\"user-data completed\"}"