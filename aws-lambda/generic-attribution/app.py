import base64
import boto3
import json
import os

bedrock = boto3.client(service_name='bedrock-runtime',
                       region_name=os.environ['AWS_REGION'])
s3 = boto3.resource('s3')
ddb = boto3.client('dynamodb')

model_id = os.environ["ModelId"]
bucket_name = os.environ["ImageBucketName"]

templates = {}

# Iterate through all template files and populate templates dictionary
for filename in os.listdir('.'):
    if filename.endswith('-template.txt'):
        template_name = filename.replace('-template.txt', '')
        with open(filename, 'r') as file:
            templates[template_name] = file.read()


def read_as_base64(bucket, key):
    # read image from s3 bucket as base64
    obj = s3.Object(bucket, key).get()
    return base64.b64encode(obj['Body'].read()).decode('utf-8')


def lambda_handler(event, context):
    print(json.dumps(event))
    prompt = templates[event["data"]["useCase"].lower()]
    id = event["data"]["id"]

    ddb.put_item(
        TableName=os.environ["TableName"],
        Item={
            'Id': {'S': id},
            'ImageBucket': {'S': bucket_name},
            'ExecutionId': {'S': event["executionId"]},
            'Progress': {'N': '40'},
            'CurrentStep': {'S': 'Prompt Template Loaded'}
        }
    )

    # Prepare the content for Converse API
    content = []

    for i in event["data"]["paths"]:
        extension = i.split(".")[-1]
        if extension.lower() == "jpg":
            extension = "jpeg"
        content.append({
            "image": {
                "format": extension,
                "source": {
                    "bytes": base64.b64decode(read_as_base64(bucket_name, i))
                }
            }
        })

    content.append({
        "text": prompt
    })

    # Make the API call using Converse API
    response = bedrock.converse(
        modelId=model_id,
        messages=[
            {
                "role": "user",
                "content": content
            }
        ],
        inferenceConfig={
            "maxTokens": 4000,
            "temperature": 1
        }
    )

    # Log token usage
    if 'usage' in response:
        usage = response['usage']
        print(f"Input tokens: {usage.get('inputTokens', 0)}, Output tokens: {usage.get('outputTokens', 0)}")

    completion = response['output']['message']['content'][0]['text']
    print(completion)

    attribution = json.loads(completion)

    update_expression = "SET Progress = :p1, CurrentStep = :p2,"
    expression_values = {":p1": {"N": "100"}, ":p2": {"S": "Attribution Generated"}}
    i = 1
    for k, v in attribution.items():
        update_expression += f"{k} = :v{str(i)},"
        expression_values[f":v{str(i)}"] = {"S": str(v)}
        i += 1

    ddb.update_item(
        TableName=os.environ["TableName"],
        Key={'Id': {'S': id}},
        UpdateExpression=update_expression[:-1],
        ExpressionAttributeValues=expression_values
    )

    return {
        'completion': completion,
        'id': id,
        'useCase': event["data"]["useCase"],
        'paths': event["data"]["paths"]
    }
