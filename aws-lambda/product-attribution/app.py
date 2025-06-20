import base64
import json
import os

import boto3

bedrock = boto3.client(service_name='bedrock-runtime',
                       region_name=os.environ['AWS_REGION'])
s3 = boto3.resource('s3')
ddb = boto3.client('dynamodb')

model_id = os.environ["ModelId"]
bucket_name = os.environ["ImageBucketName"]

with open('clothing-template.txt', 'r') as file:
    clothing_prompt = file.read()
    file.close()


def read_as_base64(bucket, key):
    # read image from s3 bucket as base64
    obj = s3.Object(bucket, key).get()
    return base64.b64encode(obj['Body'].read()).decode('utf-8')


def lambda_handler(event, context):
    print(json.dumps(event))

    id = event["data"]["id"]
    labels = event["data"]["rekognition"]["Labels"]
    tags = []
    category_tags = []
    for label in labels:
        if "Instances" in label and len(label["Instances"]) > 0:
            detected_label = label
        else:
            tags.append(label["Name"])
            tags.extend([c["Name"] for c in label["Aliases"]])
            category_tags.extend([c["Name"] for c in label["Categories"]])
            if "Parents" in label:
                for p in label["Parents"]:
                    category_tags.append(p["Name"])

    print(f"detected label: {detected_label}")
    categories = [c["Name"] for c in detected_label["Categories"]]
    categories.append(detected_label["Name"])

    bounding_box = detected_label["Instances"][0]["BoundingBox"]
    color_palette = [c["HexCode"] for c in detected_label["Instances"][0]["DominantColors"]]
    if "Parents" in detected_label:
        for p in detected_label["Parents"]:
            categories.insert(0, p["Name"])

    print(f"detected categories 3: {categories}")
    final_prompt = fill_template(clothing_prompt, detected_label, event["data"])

    ddb.put_item(
        TableName=os.environ["TableName"],
        Item={
            'Id': {'S': id},
            'ImageBucket': {'S': bucket_name},
            'InputPath': {'S': event["data"]["path"]},
            'ExecutionId': {'S': event["executionId"]},
            'ParentCategories': {'S': " > ".join(categories)},
            'RootCategory': {'S': detected_label["Name"]},
            'BoundingBox': {'M': {
                'width': {'N': str(bounding_box["Width"])},
                'height': {'N': str(bounding_box["Height"])},
                'left': {'N': str(bounding_box["Left"])},
                'top': {'N': str(bounding_box["Top"])}
            }},
            'ColorPalette': {'S': ",".join(color_palette)},
            'CategoryTags': {'S': ",".join(set(category_tags))},
            'AliasTags': {'S': ",".join(set(tags))},
            'AttributionPrompt': {'S': final_prompt},
            'Progress': {'N': '33'},
            'CurrentStep': {'S': 'Label and categories generated'}
        }
    )

    # Prepare the request body
    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4000,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": read_as_base64(bucket_name, event["data"]["path"])
                        }
                    },
                    {
                        "type": "text",
                        "text": final_prompt
                    }
                ]
            }
        ],
        "temperature": 1
    }

    # Make the API call
    response = bedrock.invoke_model(
        modelId=model_id,
        body=json.dumps(request_body)
    )

    response_body = json.loads(response['body'].read())
    completion = response_body['content'][0]['text']
    print(completion)

    attribution = json.loads(completion)

    update_expression = "SET Progress = :p1, CurrentStep = :p2,"
    expression_values = {":p1": {"N": "66"}, ":p2": {"S": "Product Attribution Generated"}}
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
        'detectedLabel': detected_label,
        'path': event["data"]["path"],
        'categoryTags': ",".join(set(category_tags)),
        'aliasTags': ",".join(set(tags)),
        'colorPalette': color_palette,
        'id': id,
        'influenceImageNumImages': event["data"]["influenceImageNumImages"],
        'influenceImagePose': event["data"]["influenceImagePose"],
        'influenceImageEmotion': event["data"]["influenceImageEmotion"],
        'influenceImageBodyStructure': event["data"]["influenceImageBodyStructure"]
    }


def fill_template(template, detected_label, event_data):
    promoted = "No"
    if event_data["isPromoted"]:
        promoted = "Yes"

    return (template.replace('{label}', detected_label["Name"])
            .replace('{brand-voice}', event_data["influenceBrandVoice"])
            .replace('{usp}', event_data["influenceBrandStrength"])
            .replace('{influence-price}', event_data["influencePrice"])
            .replace('{influenceImagePose}', event_data["influenceImagePose"])
            .replace('{influenceImageEmotion}', event_data["influenceImageEmotion"])
            .replace('{influenceImageBodyStructure}', event_data["influenceImageBodyStructure"])
            .replace('{isPromoted}', promoted)
            )
