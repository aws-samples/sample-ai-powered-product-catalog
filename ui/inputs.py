import boto3
import json
import os
import streamlit as st
import uuid

st.set_page_config(layout="wide")

bucket = os.environ["ImageBucketName"]
state_machine_arn = os.environ["StateMachineArn"]
region = os.environ["AWS_REGION"]
s3 = boto3.client("s3")
steps = boto3.client('stepfunctions', region_name=region)

c1 = st.container()
c1.title("AI-Powered Product Catalog Revolution: From Photos to Rich Listings")

c2 = st.container()
col1, col2 = c2.columns(2)

def format_body_structure_labels(label):
    if label == "Average":
        return "Build A"
    elif label == "Oversize":
        return "Build B"
    elif label == "Thin":
        return "Build C"
    return label

with col1:
    uploaded_file = st.file_uploader("Input image", type=["jpg", "jpeg", "png"])
    human_model_image = st.file_uploader("Human image (AI Generated if not provided)", type=["jpg", "jpeg", "png"])
    if uploaded_file is not None:
        image_bytes = uploaded_file.read()
        ext = uploaded_file.name.split(".")[-1]
        st.image(image_bytes, caption='Input image', use_container_width=True)

    if human_model_image is not None:
        human_model_image_bytes = human_model_image.read()
        st.image(human_model_image_bytes, use_container_width=True)

with col2:
    with st.form("Parameters"):
        gender = st.selectbox('Gender', ['male', 'female', 'unisex'], 0)
        pose = st.selectbox('Pose', ['Straight', 'Hand raised', 'Looking aside'], 0)
        body = st.selectbox(label='Body structure', options=['Average', 'Oversize', 'Thin'], index=0,
                            format_func=format_body_structure_labels)
        emotion = st.selectbox('Emotion', ['Confident', 'Amazed', 'Funny'], 0)
        strength = st.selectbox('Brand strength', ['Fabric', 'Style', 'Design', 'Competitive Pricing'], 3)
        voice = st.selectbox('Brand voice', ['Budget', 'Fun', 'Trustworthy'], 2)
        pricing_influence = st.selectbox('Pricing influence', ['Inventory Level', 'Fabric', 'Weather'], 0)
        promoted = st.toggle(label='Promoted Product')
        output_images = st.slider('Number of output images', 1, 5, 2)
        submitted = st.form_submit_button("Submit")

        if submitted:
            current_id = str(uuid.uuid4())
            path = f"input/{current_id}.{ext}"
            print("Uploading file to path: " + path)
            s3.put_object(Bucket=bucket, Key=path, Body=image_bytes)
            if human_model_image is not None:
                s3.put_object(Bucket=bucket, Key=f"input/{current_id}_model.jpg", Body=human_model_image_bytes)
                execution = steps.start_execution(stateMachineArn=state_machine_arn, input=json.dumps(
                    {"id": current_id, "path": path, "influenceImagePose": pose, "influenceImageBodyStructure": body,
                     "influenceImageEmotion": emotion, "influenceBrandStrength": strength,
                     "influenceBrandVoice": voice,
                     "humanModel": f"input/{current_id}_model.jpg",
                     "influenceGender": gender,
                     "influencePrice": pricing_influence, "isPromoted": promoted,
                     "influenceImageNumImages": output_images}))
            else:
                execution = steps.start_execution(stateMachineArn=state_machine_arn, input=json.dumps(
                    {"id": current_id, "path": path, "influenceImagePose": pose, "influenceImageBodyStructure": body,
                     "influenceImageEmotion": emotion, "influenceBrandStrength": strength,
                     "influenceBrandVoice": voice,
                     "influenceGender": gender,
                     "influencePrice": pricing_influence, "isPromoted": promoted,
                     "influenceImageNumImages": output_images}))
            print(execution["executionArn"])

            st.session_state['current_id'] = current_id
            st.switch_page("pages/outputs.py")
