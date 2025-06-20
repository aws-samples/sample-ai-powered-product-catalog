import asyncio
import boto3
import json
import os
import streamlit as st
from datetime import datetime

st.set_page_config(layout="wide")
sfn = boto3.client("stepfunctions", region_name=os.environ["AWS_REGION"])
state_machine_arn = os.environ["StateMachineArn"]

st.session_state["CurrentStep"] = 0
if "current_id" in st.query_params:
    st.session_state["current_id"] = st.query_params["current_id"]
    st.session_state["CurrentStep"] = 2
else:
    sfn_executions = sfn.list_executions(
        stateMachineArn=state_machine_arn,
        statusFilter='RUNNING',
        maxResults=1
    )
    if len(sfn_executions['executions']) > 0:
        execution_arn = sfn_executions['executions'][0]["executionArn"]
        response = sfn.describe_execution(
            executionArn=execution_arn,
            includedData='ALL_DATA'
        )
        st.session_state["current_id"] = json.loads(response["input"])['id']
    else:
        st.error("No running session found. Reload the page after submitting your input form!")
        st.stop()

all_states = ["Label and categories generated", "Product Attribution Generated", "Generating images",
              "Images Generated"]

region = os.environ["AWS_REGION"]
ddb = boto3.client("dynamodb", region_name=region)
s3 = boto3.client("s3")
table = os.environ["TableName"]

c1 = st.container()
c1.title("AI-Powered Product Catalog Revolution: From Photos to Rich Listings")
status_bar = st.status(label="Step 1: Label detection and Image Analysis", state="running")
progress_bar = st.progress(0, text="Analyzing input image")

current_id = st.session_state['current_id']
c2 = st.container()
col1, col2 = c2.columns(2)

with col1:
    carousal = st.empty()
    carousal.image("img/loader.gif")

with col2:
    breadcrum = st.empty()
    title = st.empty()
    description = st.empty()
    st.divider()
    attribution = st.empty()
    st.divider()
    tags = st.empty()
    categories = st.empty()
    st.divider()
    pallet = st.empty()

st.subheader("Previous Outputs - History")
c3 = st.container()
footer_cols = c3.columns(10)
i = 0
if "history" in st.session_state:
    # print(st.session_state["history"])
    for h_item in st.session_state["history"]:
        obj = s3.get_object(Bucket=h_item["bucket"], Key=h_item["path"])
        footer_cols[i].image(obj['Body'].read(), use_container_width=True)
        footer_cols[i].markdown("["+str(i)+"](?current_id=" + h_item["id"]+")")
        i += 1


async def async_updates():
    st.session_state["WrittenKeys"] = set()
    st.session_state["Attribution"] = ""
    max_iterations = 30
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

            if item["CurrentStep"]["S"] == all_states[0] and st.session_state["CurrentStep"] == 0:
                with status_bar:
                    st.session_state["CurrentStep"] = 1
                    st.subheader("Step 1: Label detection and Image Analysis", divider="rainbow")
                    status_bar_11, status_bar_12, status_bar_13 = st.columns(3)
                    obj = s3.get_object(Bucket=item["ImageBucket"]["S"], Key=item["InputPath"]["S"])
                    status_bar_11.image(obj['Body'].read(), use_container_width=True)
                    status_bar_12.image("img/rekognition.png")
                    status_bar_13.markdown(
                        "As a first step we analyze image using Amazon Rekognition service to identify labels, color pallet of a detected object and the hierarchy of a product category from the clothing image.")
                status_bar.update(label="Step 2: Generating Product Attribution", state="running")
            elif (item["CurrentStep"]["S"] == all_states[1] or item["CurrentStep"]["S"] == all_states[2]) and \
                    st.session_state["CurrentStep"] == 1:
                status_bar.update(label="Step 2: Product Attribution Generated", state="running")
                with status_bar:
                    st.session_state["CurrentStep"] = 2
                    st.subheader("Step 2: Product Attribution", divider="rainbow")
                    status_bar_11, status_bar_12, status_bar_13 = st.columns(3)
                    status_bar_11.image("img/bedrock.png")
                    status_bar_11.text("Same input image")
                    status_bar_12.markdown("***Prompt:***\n\n" + item["AttributionPrompt"]["S"])
                    status_bar_13.markdown(
                        "As a second step we use Amazon Bedrock Claude model to analyze the clothing image and generate product attributes. The process includes extracting labels from Rekognition, identifying categories, color palette, and bounding boxes, then generating detailed product information like title, description, and predicted price while incorporating brand voice parameters.")
                status_bar.update(label="Step 3: Generating Images", state="running")
            elif item["CurrentStep"]["S"] == all_states[3] and st.session_state["CurrentStep"] == 2:
                with status_bar:
                    st.session_state["CurrentStep"] = 3
                    st.subheader("Step 3: Generate image variations based on LLM Generated Prompt",
                                 divider="rainbow")
                    status_bar_11, status_bar_12, status_bar_13, status_bar_14 = st.columns(4)
                    status_bar_11_splits = status_bar_11.columns(2)
                    k = 0
                    for i in item["ReferenceImages"]["L"]:
                        obj = s3.get_object(Bucket=item["ImageBucket"]["S"], Key=i["S"])
                        image_bytes = obj['Body'].read()
                        status_bar_11_splits[k % 2].image(image_bytes)
                        k += 1
                    status_bar_12.markdown("***Prompt:***\n\n" + item["ImageGeneratorPrompt"]["S"])
                    status_bar_13_splits = status_bar_13.columns(2)
                    k = 0
                    for i in item["OutputImages"]["L"]:
                        # Display output image
                        obj = s3.get_object(Bucket=item["ImageBucket"]["S"], Key=i["S"])
                        image_bytes = obj['Body'].read()
                        status_bar_13_splits[k % 2].image(image_bytes)

                        k += 1
                    status_bar_14.markdown(
                        "As a last step \n 1. We ask Amazon Bedrock Nova Canvas model to generate human model image in requested pose & emotion. This step is skipped if human model image is already provided. \n 2. We classify the garment type using Amazon Bedrock Nova Pro model to determine the appropriate clothing category. \n 3. Using Amazon Bedrock Nova Canvas VIRTUAL_TRY_ON capability, we generate both the final try-on image and the corresponding mask image showing the clothing segmentation. \n 4. The mask images help visualize how the model identifies the clothing regions for accurate virtual try-on.")
                status_bar.update(label="Step 3: Images Generated", state="complete")

            if "history" in st.session_state:
                if current_id not in [c["id"] for c in st.session_state["history"]]:
                    st.session_state["history"].append(
                        {"id": current_id, "bucket": item["ImageBucket"]["S"], "path": item["InputPath"]["S"]})
            else:
                st.session_state["history"] = [
                    {"id": current_id, "bucket": item["ImageBucket"]["S"], "path": item["InputPath"]["S"]}]
            if len(st.session_state["history"]) > 10:
                st.session_state["history"].pop(0)
        if "CategoryTags" in item and "CategoryTags" not in st.session_state["WrittenKeys"]:
            with categories.container():
                md = "**Other Categories:**\n"
                for t in item["CategoryTags"]["S"].split(","):
                    md += f":green-background[{t}] "
                st.markdown(md)
                st.session_state["WrittenKeys"].add("CategoryTags")
        if "AliasTags" in item and "AliasTags" not in st.session_state["WrittenKeys"]:
            with tags.container():
                md = "**Tags:**\n"
                for t in item["AliasTags"]["S"].split(","):
                    md += f":blue-background[{t}] "
                st.markdown(md)
                st.session_state["WrittenKeys"].add("AliasTags")
        if "ColorPalette" in item and "ColorPalette" not in st.session_state["WrittenKeys"]:
            with pallet.container():
                st.session_state["WrittenKeys"].add("ColorPalette")
                st.color_picker(value=item["ColorPalette"]["S"].split(",")[0], key="C1", label="Primary Color")
                st.color_picker(value=item["ColorPalette"]["S"].split(",")[1], key="C2", label="Secondary Color")
                st.color_picker(value=item["ColorPalette"]["S"].split(",")[2], key="C3", label="Last Color")
        if "Title" in item and "Title" not in st.session_state["WrittenKeys"]:
            with title.container():
                st.session_state["WrittenKeys"].add("Title")
                st.subheader(item["Title"]["S"], divider=True)
        if "Description" in item and "Description" not in st.session_state["WrittenKeys"]:
            with description.container():
                st.session_state["WrittenKeys"].add("Description")
                st.markdown(item["Description"]["S"])
        if "ParentCategories" in item and "ParentCategories" not in st.session_state["WrittenKeys"]:
            with breadcrum.container():
                st.session_state["WrittenKeys"].add("ParentCategories")
                st.subheader(item["ParentCategories"]["S"], divider=True)
        # Create a dictionary to track which images to display
        display_images = {}
        
        # Initialize with reference images (human models)
        if "ReferenceImages" in item:
            for i in item["ReferenceImages"]["L"]:
                if "input/" not in i["S"] and "human-model-images" in i["S"]:
                    # Extract the index from the path (e.g., human-model-images/id/1.png -> 1)
                    img_index = i["S"].split('/')[-1].split('.')[0]
                    display_images[img_index] = {
                        "bucket": item["ImageBucket"]["S"],
                        "key": i["S"],
                        "type": "reference"
                    }
        
        # Override with output images where available (virtual try-on results)
        if "OutputImages" in item:
            for i in item["OutputImages"]["L"]:
                # Extract the index from the path (e.g., output/.../1.jpg -> 1)
                img_index = i["S"].split('/')[-1].split('.')[0]
                display_images[img_index] = {
                    "bucket": item["ImageBucket"]["S"],
                    "key": i["S"],
                    "type": "output"
                }
        
        # Display the images in the carousel
        if display_images and (
            "ReferenceImages" not in st.session_state["WrittenKeys"] or 
            "OutputImages" not in st.session_state["WrittenKeys"] or
            len(display_images) > len(st.session_state.get("last_displayed_images", []))
        ):
            with carousal.container():
                # Track which types of images we've displayed
                has_reference = False
                has_output = False
                
                # Display all images
                for img_data in display_images.values():
                    obj = s3.get_object(Bucket=img_data["bucket"], Key=img_data["key"])
                    image_bytes = obj['Body'].read()
                    st.image(image_bytes, use_container_width=True)
                    
                    if img_data["type"] == "reference":
                        has_reference = True
                    elif img_data["type"] == "output":
                        has_output = True
                
                # Update tracking in session state
                if has_reference:
                    st.session_state["WrittenKeys"].add("ReferenceImages")
                if has_output:
                    st.session_state["WrittenKeys"].add("OutputImages")
                    st.session_state["WrittenKeys"].add("ImageBucket")
                
                # Store what we displayed for comparison on next update
                st.session_state["last_displayed_images"] = list(display_images.keys())

        text = st.session_state["Attribution"]
        for e in item.keys():
            if "S" in item[e] and e not in st.session_state["WrittenKeys"] and "Prompt" not in e:
                st.session_state["WrittenKeys"].add(e)
                if item[e]["S"] != "":
                    text += "- **" + e + ":** " + item[e]["S"] + "\n"

        attribution.markdown(text)
        st.session_state["Attribution"] = text

        _ = await asyncio.sleep(5)


asyncio.run(async_updates())
