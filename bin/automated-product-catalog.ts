#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import {Aspects} from 'aws-cdk-lib';
import {AutomatedProductCatalogStack} from '../lib/automated-product-catalog-stack';
import {AwsSolutionsChecks} from "cdk-nag";

const app = new cdk.App();
Aspects.of(new AutomatedProductCatalogStack(app, 'AutomatedProductCatalogStack', {
    env: {
        region: process.env.CDK_DEFAULT_REGION,
        account: process.env.CDK_DEFAULT_ACCOUNT
    }
}))
    .add(new AwsSolutionsChecks({verbose: true}));