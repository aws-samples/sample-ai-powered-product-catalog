import * as cdk from 'aws-cdk-lib';
import {Aws, CfnOutput, Duration} from 'aws-cdk-lib';
import {Construct} from 'constructs';
import {DefinitionBody, LogLevel, StateMachine} from "aws-cdk-lib/aws-stepfunctions";
import {BlockPublicAccess, Bucket} from "aws-cdk-lib/aws-s3";
import {Code, Function, Runtime} from "aws-cdk-lib/aws-lambda";
import {PolicyStatement} from "aws-cdk-lib/aws-iam";
import {AttributeType, Table, TableEncryption} from "aws-cdk-lib/aws-dynamodb";
import {DockerImageAsset, Platform} from "aws-cdk-lib/aws-ecr-assets";
import {GatewayVpcEndpointAwsService, InterfaceVpcEndpointAwsService, SubnetType, Vpc} from "aws-cdk-lib/aws-ec2";
import {NagSuppressions} from "cdk-nag";
import {PythonFunction} from "@aws-cdk/aws-lambda-python-alpha";


export class AutomatedProductCatalogStack extends cdk.Stack {
    constructor(scope: Construct, id: string, props?: cdk.StackProps) {
        super(scope, id, props);

        NagSuppressions.addStackSuppressions(this, [
            {
                id: "AwsSolutions-S1",
                reason: "Demo S3 bucket used to store only temporary images, hence server access logs not enabled"
            },
            {
                id: "AwsSolutions-VPC7",
                reason: "Skipping VPC Flow Logs as this is a demo environment"
            },
            {
                id: "AwsSolutions-IAM4",
                reason: "Using AWS managed policy AWSLambdaBasicExecutionRole which provides minimal permissions for Lambda execution",
                appliesTo: ['Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole']
            },
            {
                id: "AwsSolutions-IAM5",
                reason: "Lambda functions require wildcard permissions for S3 operations on specific paths (input/*, output/*, human-model-images/*) and Lambda invocation permissions",
            },
            {
                id: "AwsSolutions-EC29",
                reason: "Demo EC2 instance does not require termination protection as it's for demonstration purposes"
            },
            {
                id: "CdkNagValidationFailure",
                reason: "Security group validation failures due to intrinsic function references in VPC CIDR blocks are acceptable for this demo"
            }
        ])

        const imagesBucket = new Bucket(this, "images", {
            enforceSSL: true,
            blockPublicAccess: BlockPublicAccess.BLOCK_ALL,
            autoDeleteObjects: true,
            removalPolicy: cdk.RemovalPolicy.DESTROY
        });

        const table = new Table(this, "ProductDrafts", {
            partitionKey: {name: "Id", type: AttributeType.STRING},
            encryption: TableEncryption.AWS_MANAGED,
            pointInTimeRecovery: true
        });

        const textModelId = "amazon.nova-pro-v1:0";
        const imageModelId = "amazon.nova-canvas-v1:0";
        const productAttributionFn = new Function(this, "ProductAttributionFn", {
            code: Code.fromAsset("./aws-lambda/product-attribution"),
            handler: "app.lambda_handler",
            runtime: Runtime.PYTHON_3_13,
            environment: {
                "ImageBucketName": imagesBucket.bucketName,
                "ModelId": textModelId,
                "TableName": table.tableName
            },
            timeout: Duration.minutes(1)
        });
        productAttributionFn.addToRolePolicy(new PolicyStatement({
            actions: ["bedrock:InvokeModel"],
            resources: ["arn:aws:bedrock:" + Aws.REGION + "::foundation-model/" + textModelId]
        }));

        const genericAttributionFn = new Function(this, "GenericAttributionFn", {
            code: Code.fromAsset("./aws-lambda/generic-attribution"),
            handler: "app.lambda_handler",
            runtime: Runtime.PYTHON_3_13,
            environment: {
                "ImageBucketName": imagesBucket.bucketName,
                "ModelId": textModelId,
                "TableName": table.tableName
            },
            timeout: Duration.minutes(1)
        });
        genericAttributionFn.addToRolePolicy(new PolicyStatement({
            actions: ["bedrock:InvokeModel"],
            resources: ["arn:aws:bedrock:" + Aws.REGION + "::foundation-model/" + textModelId]
        }));

        // Nova Canvas is used for virtual try-on
        const imageGenerationTryOn = new PythonFunction(this, "ImageGenerationTryOnFn", {
            entry: "./aws-lambda/image-try-on",
            handler: "lambda_handler",
            runtime: Runtime.PYTHON_3_13,
            environment: {
                "ImageBucketName": imagesBucket.bucketName,
                "TableName": table.tableName,
                "ModelId": imageModelId,
            },
            timeout: Duration.minutes(2)
        });
        imageGenerationTryOn.addToRolePolicy(new PolicyStatement({
            actions: ["bedrock:InvokeModel"],
            resources: [
                // For Nova Canvas virtual try-on
                "arn:aws:bedrock:" + Aws.REGION + "::foundation-model/" + imageModelId,
                // For Nova Pro garment classification
                "arn:aws:bedrock:" + Aws.REGION + "::foundation-model/" + textModelId
            ]
        }));

        const logGroup = new cdk.aws_logs.LogGroup(this, "stateMachineLogGroup");
        const stepFn = new StateMachine(this, "stateMachine", {
            timeout: Duration.minutes(10),
            definitionBody: DefinitionBody.fromFile("./workflow.asl.json"),
            definitionSubstitutions: {
                "ImageBucketName": imagesBucket.bucketName,
                "ProductAttributionFnArn": productAttributionFn.functionArn,
                "ImageGenerationFnArn": imageGenerationTryOn.functionArn,
            },
            tracingEnabled: true,
            logs: {level: LogLevel.ALL, includeExecutionData: false, destination: logGroup}
        });

        const attrStepFn = new StateMachine(this, "attributionStateMachine", {
            timeout: Duration.minutes(10),
            definitionBody: DefinitionBody.fromFile("./workflow-attribution.asl.json"),
            definitionSubstitutions: {
                "ProductAttributionFnArn": genericAttributionFn.functionArn
            },
            tracingEnabled: true,
            logs: {level: LogLevel.ALL, includeExecutionData: false, destination: logGroup}
        });

        stepFn.addToRolePolicy(new PolicyStatement({
            actions: ["rekognition:DetectLabels"],
            resources: ["*"]
        }));
        imagesBucket.grantRead(stepFn, "input/*")
        imagesBucket.grantRead(attrStepFn, "input/*")
        imagesBucket.grantRead(genericAttributionFn, "input/*")
        imagesBucket.grantRead(productAttributionFn, "input/*")
        imagesBucket.grantRead(imageGenerationTryOn, "input/*")
        imagesBucket.grantReadWrite(imageGenerationTryOn, "human-model-images/*")
        imagesBucket.grantWrite(imageGenerationTryOn, "output/*")
        genericAttributionFn.grantInvoke(attrStepFn);
        productAttributionFn.grantInvoke(stepFn);
        imageGenerationTryOn.grantInvoke(stepFn);
        table.grant(genericAttributionFn, "dynamodb:PutItem");
        table.grant(genericAttributionFn, "dynamodb:UpdateItem");
        table.grant(productAttributionFn, "dynamodb:PutItem");
        table.grant(productAttributionFn, "dynamodb:UpdateItem");
        table.grant(imageGenerationTryOn, "dynamodb:UpdateItem");

        // Add CloudFormation outputs for local development
        new CfnOutput(this, 'ImageBucketName', {
            value: imagesBucket.bucketName,
            description: 'Name of the S3 bucket for storing images'
        });

        new CfnOutput(this, 'StateMachineArn', {
            value: stepFn.stateMachineArn,
            description: 'ARN of the Step Function state machine'
        });

        new CfnOutput(this, 'AttributionStateMachineArn', {
            value: attrStepFn.stateMachineArn,
            description: 'ARN of the Step Function state machine'
        });

        new CfnOutput(this, 'TableName', {
            value: table.tableName,
            description: 'Name of the DynamoDB table for product drafts'
        });

        const vpc = new Vpc(this, "StreamlitVPC", {
            maxAzs: 2,
            natGateways: 0,
            subnetConfiguration: [{name: "isolated", subnetType: SubnetType.PRIVATE_ISOLATED}],
            gatewayEndpoints: {
                "S3": {service: GatewayVpcEndpointAwsService.S3},
                "DYNAMODB":   {service: GatewayVpcEndpointAwsService.DYNAMODB}
            }
        });
        // System manager access for SSM Tunneling to EC2 and to install docker on EC2
        vpc.addInterfaceEndpoint("ssm", {
            service: InterfaceVpcEndpointAwsService.SSM
        });
        vpc.addInterfaceEndpoint("ssm-messages", {
            service: InterfaceVpcEndpointAwsService.SSM_MESSAGES
        });
        vpc.addInterfaceEndpoint("ec2-messages", {
            service: InterfaceVpcEndpointAwsService.EC2_MESSAGES
        });
        
        // Allow EC2 to download docker image and invoke Step function
        vpc.addInterfaceEndpoint("ecr", {
            service: InterfaceVpcEndpointAwsService.ECR
        });
        vpc.addInterfaceEndpoint("docker-ecr", {
            service: InterfaceVpcEndpointAwsService.ECR_DOCKER
        });
        vpc.addInterfaceEndpoint("sfn", {
            service: InterfaceVpcEndpointAwsService.STEP_FUNCTIONS
        });

        const streamlitDkrImage = new DockerImageAsset(this, "streamlit-ui", {
            directory: "./ui/",
            platform: Platform.LINUX_AMD64
        });

        // Create the EC2 instance in a public subnet but with no inbound access
        const ec2Instance = new cdk.aws_ec2.Instance(this, 'StreamlitEC2Instance', {
            vpc: vpc,
            blockDevices: [{
                deviceName: '/dev/xvda',
                mappingEnabled: true,
                volume: cdk.aws_ec2.BlockDeviceVolume.ebs(8, {
                    deleteOnTermination: true,
                    volumeType: cdk.aws_ec2.EbsDeviceVolumeType.GP3,
                    encrypted: true
                })
            }],
            detailedMonitoring: true,
            vpcSubnets: {
                subnetType: cdk.aws_ec2.SubnetType.PRIVATE_ISOLATED
            },
            instanceType: cdk.aws_ec2.InstanceType.of(
                cdk.aws_ec2.InstanceClass.M5,
                cdk.aws_ec2.InstanceSize.LARGE
            ),
            machineImage: cdk.aws_ec2.MachineImage.latestAmazonLinux2023({
                cpuType: cdk.aws_ec2.AmazonLinuxCpuType.X86_64
            })
        });

        // Grant permissions to the EC2 instance
        stepFn.grantStartExecution(ec2Instance);
        stepFn.grantRead(ec2Instance);
        imagesBucket.grantRead(ec2Instance);
        imagesBucket.grantWrite(ec2Instance, "input/*");
        table.grantReadData(ec2Instance);

        const userData = cdk.aws_ec2.UserData.forLinux();
        const dockerImageUri = streamlitDkrImage.imageUri;
        streamlitDkrImage.repository.grantPull(ec2Instance);


        userData.addCommands(
            'yum update -y',
            'yum install -y docker',
            'systemctl start docker',
            'systemctl enable docker',
            // Login to ECR to pull the image
            `aws ecr get-login-password --region ${Aws.REGION} | docker login --username AWS --password-stdin ${Aws.ACCOUNT_ID}.dkr.ecr.${Aws.REGION}.amazonaws.com`,
            `docker pull ${dockerImageUri}`,
            // Run the Streamlit container
            `docker run -d --name streamlit -p 8501:8501 \\
            -e AWS_REGION=${Aws.REGION} \\
            -e StateMachineArn=${stepFn.stateMachineArn} \\
            -e ImageBucketName=${imagesBucket.bucketName} \\
            -e TableName=${table.tableName} \\
            -e IS_LOCAL=true \\
            ${dockerImageUri}`
        );

        // Apply the UserData to the instance
        ec2Instance.addUserData(...userData.render().split('\n'));
    }
}
