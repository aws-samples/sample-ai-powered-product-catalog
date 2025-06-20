import boto3
import asyncio
from datetime import datetime
import json
import os
import streamlit as st
import uuid

st.set_page_config(layout="wide")

bucket = os.environ["ImageBucketName"]
state_machine_arn = os.environ["AttributionStateMachineArn"]
region = os.environ["AWS_REGION"]
table = os.environ["TableName"]
s3 = boto3.client("s3")
steps = boto3.client('stepfunctions', region_name=region)
ddb = boto3.client("dynamodb", region_name=region)

c1 = st.container()
c1.title("Attribution Deep Dive: From Photos to Rich Listings")
progress_bar = st.progress(0, text="Analyzing input image")

c2 = st.container()
col1, col2 = c2.columns(2)

with col1:
    with st.form("Parameters"):
        current_id = str(uuid.uuid4())
        use_case = st.selectbox('Use case', ['Hospitality'], 0)
        input_images = st.file_uploader(
            "Upload images (up to 5)", 
            type=["jpg", "jpeg", "png"], 
            accept_multiple_files=True,
            help="Select up to 5 images for attribution analysis"
        )

        submitted = st.form_submit_button("Submit")
        if submitted:
            paths = []
            if input_images:
                # Limit to maximum 5 files
                files_to_process = input_images[:5]
                
                for i, input_image in enumerate(files_to_process, 1):
                    ext = input_image.name.split(".")[-1]
                    path = f"input/attributions/{current_id}_{i}.{ext}"
                    paths.append(path)
                    img_bytes = input_image.read()
                    s3.put_object(Bucket=bucket, Key=path, Body=img_bytes)
                    st.image(img_bytes, use_container_width=True)

            if paths:  # Only start execution if we have images to process
                execution = steps.start_execution(stateMachineArn=state_machine_arn,
                                                  input=json.dumps({"id": current_id, "useCase": use_case, "paths": paths}))
                st.session_state['current_id'] = current_id
            else:
                st.error("Please upload at least one image before submitting.")

with col2:
    attribution = st.empty()


async def async_updates():
    st.session_state["WrittenKeys"] = []
    st.session_state["Attribution"] = ""
    max_iterations = 5
    progress = 0
    while max_iterations > 0 and progress < 100:
        max_iterations -= 1
        resp = ddb.get_item(TableName=table, Key={"Id": {"S": current_id}})
        item = {}
        if "Item" in resp:
            item = resp["Item"]
            print(str(datetime.now()) + " > " + json.dumps(item))
            progress = int(item["Progress"]["N"])
            progress_bar.progress(progress, text=item["CurrentStep"]["S"])

            text = st.session_state["Attribution"]
            for e in item.keys():
                if "S" in item[e] and e not in st.session_state["WrittenKeys"] and "Prompt" not in e:
                    st.session_state["WrittenKeys"].append(e)
                    if item[e]["S"] != "":
                        text += "- **" + e + ":** " + item[e]["S"] + "\n"

            attribution.markdown(text)
            st.session_state["Attribution"] = text

        _ = await asyncio.sleep(5)


asyncio.run(async_updates())
