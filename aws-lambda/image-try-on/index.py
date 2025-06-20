import base64
import boto3
import io
import json
import os
import random
import imagesize
import concurrent.futures
# Using native Python 3.13 typing features

bucket_name = os.environ["ImageBucketName"]
image_gen_model_id = os.environ["ModelId"]
bedrock = boto3.client('bedrock-runtime', region_name=os.environ["AWS_REGION"])
s3_resource = boto3.resource("s3")
s3 = boto3.client("s3")
ddb = boto3.client("dynamodb")


def read_as_base64(bucket, key):
    # read image from s3 bucket as base64
    obj = s3_resource.Object(bucket, key).get()
    return base64.b64encode(obj['Body'].read()).decode('utf-8')


def get_image_dimensions(image_base64):
    """
    Get the dimensions of a base64 encoded image using imagesize
    
    Args:
        image_base64: Base64 encoded image string
        
    Returns:
        Tuple of (width, height)
    """
    try:
        # Convert base64 to image bytes
        image_bytes = base64.b64decode(image_base64)
        # Create a BytesIO object from the image bytes
        with io.BytesIO(image_bytes) as f:
            # Get image size using imagesize
            width, height = imagesize.get(f)
            return width, height
    except Exception as e:
        print(f"Error getting image dimensions: {str(e)}")
        # Return default dimensions if there's an error
        return 1024, 1024


def classify_garment(image_base64, extension):
    """
    Classify the type of garment in an image using Amazon Bedrock Nova Pro model.
    
    Args:
        image_base64: Base64 encoded image string
        extension: Extension of the image gif, jpeg, png, webp
        
    Returns:
        String representing the garment type classification
    """
    try:
        # Convert base64 to image bytes
        image_bytes = base64.b64decode(image_base64)
        if extension.lower() == "jpg":
            extension = "jpeg"
        
        system_prompt = [{'text': 'You are an expert in clothing classification. You will be given an image. You will be asked to determine the type of garment in the image. You will be asked to generate output in json format following a schema.'}]

        user_prompt_content = [
            {'text': 'Here is an image. Determine the type of garment in this image. Find the most accurate garment type from this list: ["LONG_SLEEVE_SHIRT", "SHORT_SLEEVE_SHIRT", "NO_SLEEVE_SHIRT", "UPPER_BODY", "LONG_PANTS", "SHORT_PANTS", "LOWER_BODY", "LONG_DRESS", "SHORT_DRESS", "FULL_BODY", "SHOES", "BOOTS", "FOOTWEAR", "FULL_BODY_OUTFIT"]. If you cannot find an option, find the most accurate garment type from this list: ["OTHER_UPPER_BODY", "OTHER_LOWER_BODY", "OTHER_FULL_BODY", "OTHER_FOOTWEAR"].'},
            {'image': {'format': extension.lower(), 'source': {'bytes': image_bytes}}},
            {'text': 'Output in json format following schema: {"type": "object", "properties": {"garment_type": {"type": "string", "enum": ["LONG_SLEEVE_SHIRT", "SHORT_SLEEVE_SHIRT", "NO_SLEEVE_SHIRT", "UPPER_BODY", "LONG_PANTS", "SHORT_PANTS", "LOWER_BODY", "LONG_DRESS", "SHORT_DRESS", "FULL_BODY", "SHOES", "BOOTS", "FOOTWEAR", "FULL_BODY_OUTFIT", "OTHER_UPPER_BODY", "OTHER_LOWER_BODY", "OTHER_FULL_BODY", "OTHER_FOOTWEAR"]}}, "required": ["garment_type"]}'}
        ]
        prompt_messages = [{'role': 'user', 'content': user_prompt_content}]

        # Use the bedrock client that's already initialized
        model_response = bedrock.converse(
            modelId='amazon.nova-pro-v1:0',
            messages=prompt_messages,
            system=system_prompt
        )
        generated_text = model_response['output']['message']['content'][0]['text']
        garment_class_analysis = json.loads(generated_text)
        return garment_class_analysis['garment_type']
    except Exception as e:
        print(f"Error in classify_garment: {str(e)}")
        return "UPPER_BODY"


def apply_nova_virtual_try_on(
    source_image_base64,
    reference_image_base64,
    garment_class
):
    """
    Apply virtual try-on using Amazon Bedrock's Nova Canvas model.
    
    Args:
        source_image_base64: Base64 encoded string of the person/model image
        reference_image_base64: Base64 encoded string of the garment image
        garment_class: Type of garment classification
        
    Returns:
        Dictionary containing the response with 'images' (list of base64 strings) and 'maskImage' (single base64 string)
    """
    # Prepare payload for Nova Canvas
    payload = {
        'taskType': 'VIRTUAL_TRY_ON',
        'virtualTryOnParams': {
            'sourceImage': source_image_base64,
            'referenceImage': reference_image_base64,
            'maskType': 'GARMENT',
            'garmentBasedMask': {
                'garmentClass': garment_class,
                "maskShape": "BOUNDING_BOX",
            }
        },
        'imageGenerationConfig': {
            "numberOfImages": 1,
            "cfgScale": 6.5,
            # 'quality': 'premium',
            'seed': random.randint(0, 100000000)
        }
    }

    body_json = json.dumps(payload)

    # Use the existing bedrock client
    try:
        response = bedrock.invoke_model(
            body=body_json,
            modelId=image_gen_model_id,
            accept='application/json',
            contentType='application/json',
        )
        response_body = json.loads(response.get('body').read())

        if 'error' in response_body:
            raise Exception(f"Error in response: {response_body['error']}")

        # Return the complete response body containing images and maskImage
        return response_body
    except Exception as e:
        print(f"Error in Nova Canvas virtual try-on: {str(e)}")
        raise

def lambda_handler(event, context):
    pose = event["influenceImagePose"]
    emotion = event["influenceImageEmotion"]
    body_structure = event["influenceImageBodyStructure"]
    num_images = event["influenceImageNumImages"]
    gender = event["influenceGender"]
    id = event["id"]

    reference_images = []
    reference_images_prefixes = []

    # Get the cloth image first to extract dimensions
    cloth_image = read_as_base64(bucket_name, event["path"])
    
    # Extract dimensions from the cloth image
    width, height = get_image_dimensions(cloth_image)
    
    if "humanModel" in event:
        # If humanModel is passed via webcam then use it as viton target else generate human models
        generated_images = [read_as_base64(bucket_name, event["humanModel"])]
        prompt = "Human model input from web cam"
    else:
        # 1. Generate model images from the text
        prompt = f"realistic full body length photo of a {gender} fashion model, {body_structure} body structure, {emotion}, wearing a plain white t-shirt, standing in a {pose} front facing pose against a plain background, studio lighting"
        generated_images = generate_model_image(num_images, prompt, width, height)
        reference_images.extend(generated_images)

    # Classify the garment type from the cloth image
    try:
        print("Classifying garment...")
        garment_type = classify_garment(cloth_image, event["path"].split('.')[-1])
        print(f"Classified garment as: {garment_type}")
    except Exception as e:
        print(f"Error classifying garment: {str(e)}")
        garment_type = "UPPER_BODY"

    # Save reference images to S3
    counter = 1
    for i, img_base64 in enumerate(generated_images):
        image_bytes = base64.b64decode(img_base64)
        prefix = f"human-model-images/{id}/{counter}.png"
        s3.put_object(Body=image_bytes, Bucket=bucket_name, Key=prefix)
        reference_images_prefixes.append({"S": prefix})
        counter += 1

    reference_images_prefixes.append({"S": event["path"]})
    
    # Update progress to DDB
    ddb.update_item(
        TableName=os.environ["TableName"],
        Key={"Id": {"S": event["id"]}},
        UpdateExpression="SET ImageGeneratorPrompt = :v1, ReferenceImages = :v2, Progress = :v3, CurrentStep = :v4, GarmentType = :v5",
        ExpressionAttributeValues={
            ":v1": {"S": prompt},
            ":v2": {"L": reference_images_prefixes},
            ":v3": {"N": "80"}, 
            ":v4": {"S": "Generating images"},
            ":v5": {"S": garment_type}
        }
    )

    # Process a single try-on operation
    def process_single_try_on(args):
        """
        Process a single virtual try-on operation
        
        Args:
            args: Tuple containing (source_image_base64, index, cloth_image, garment_type)
            
        Returns:
            Dictionary with output image key and mask key (if available)
        """
        source_image_base64, index, cloth_image, garment_type = args
        result = {}
        
        try:
            # Apply virtual try-on using Nova
            try_on_result = apply_nova_virtual_try_on(
                source_image_base64=source_image_base64,
                reference_image_base64=cloth_image,
                garment_class=garment_type
            )
            
            # Save result image
            image_bytes = base64.b64decode(try_on_result['images'][0])
            key = event["path"].replace("input/", "output/").replace(".jpg", "").replace(".png", "") + f"/{index}.jpg"
            s3.put_object(Body=image_bytes, Bucket=bucket_name, Key=key)
            result["output_key"] = key
            
            # Update DynamoDB with this output image immediately
            try:
                ddb.update_item(
                    TableName=os.environ["TableName"],
                    Key={"Id": {"S": event["id"]}},
                    UpdateExpression="SET OutputImages = list_append(if_not_exists(OutputImages, :empty_list), :new_image)",
                    ExpressionAttributeValues={
                        ":new_image": {"L": [{"S": key}]},
                        ":empty_list": {"L": []}
                    }
                )
                print(f"Updated DynamoDB with new output image: {key}")
            except Exception as e:
                print(f"Error updating DynamoDB with output image: {str(e)}")
            
            # Save mask image if available
            if 'maskImage' in try_on_result:
                mask_image_bytes = base64.b64decode(try_on_result['maskImage'])
                mask_key = event["path"].replace("input/", "output/").replace(".jpg", "").replace(".png", "") + f"/{index}_mask.jpg"
                s3.put_object(Body=mask_image_bytes, Bucket=bucket_name, Key=mask_key)
                result["mask_key"] = mask_key
                
            return {"success": True, "output_key": key, "index": index}
                
        except Exception as e:
            print(f"Error in virtual try-on for index {index}: {str(e)}")
            return {"success": False, "error": str(e), "index": index}
    
    # Apply Nova virtual try-on in parallel for all generated human models
    try_on_tasks = []
    for i, source_image_base64 in enumerate(generated_images):
        try_on_tasks.append((source_image_base64, i + 1, cloth_image, garment_type))
    
    # Use ThreadPoolExecutor to process try-on operations in parallel
    # Adjust max_workers based on Lambda's capabilities and API rate limits
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, len(generated_images))) as executor:
        results = list(executor.map(process_single_try_on, try_on_tasks))
    
    # Process results to check for any failures
    failed_results = [result for result in results if not result.get("success", False)]
    if failed_results:
        print(f"Warning: {len(failed_results)} out of {len(results)} try-on operations failed")

    # Update DDB with final progress and status only
    # (OutputImages are already updated in real-time during processing)
    ddb.update_item(
        TableName=os.environ["TableName"],
        Key={"Id": {"S": event["id"]}},
        UpdateExpression="SET Progress = :v1, CurrentStep = :v2",
        ExpressionAttributeValues={
            ":v1": {"N": "100"},
            ":v2": {"S": "Images Generated"}
        }
    )



def generate_model_image(num_images, prompt, width=1024, height=1024):
    body = json.dumps({
        "taskType": "TEXT_IMAGE",
        "textToImageParams": {
            "text": prompt,
            "negativeText": "bad quality, low res, cartoon, unreal, head cropped, blur"
        },
        "imageGenerationConfig": {
            "numberOfImages": num_images,
            "height": height,
            "width": width,
            "seed": random.randint(0, 100000000)
        }
    })

    response = bedrock.invoke_model(
        body=body, modelId=image_gen_model_id, accept="application/json", contentType="application/json"
    )
    response_body = json.loads(response.get("body").read())
    return response_body.get("images")
