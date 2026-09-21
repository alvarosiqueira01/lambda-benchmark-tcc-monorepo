"""Lambda especializada: metricas de legibilidade.

Somente stdlib via common.analysis; sem boto3 e sem DynamoDB (RNF-04).
"""

from common.analysis import readability_analysis


def handler(event, context) -> dict:
    return readability_analysis(event["body"], event["language"], event["tags"])