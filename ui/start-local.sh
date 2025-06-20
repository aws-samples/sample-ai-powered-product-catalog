export AWS_REGION=us-east-1
export IS_LOCAL=true
eval $(aws cloudformation describe-stacks --stack-name AutomatedProductCatalogStack --region ${AWS_REGION} | jq -r '.Stacks[0].Outputs[] | "export \(.OutputKey)=\(.OutputValue)"')
streamlit run inputs.py
