export REGION=us-east-1
export BUCKET=saimukeshreddyvanchas3

aws s3api create-bucket --bucket "$BUCKET" --region $REGION


# upload the local portfolio to the s3 bucket 
aws s3 sync ../portfolio "s3://$BUCKET/webroot"