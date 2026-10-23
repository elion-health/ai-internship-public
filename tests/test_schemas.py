from ai_internship.schemas import CustomerAnalysis


def test_customer_analysis_schema_forbids_extra_properties_for_openai_response_format():
    schema = CustomerAnalysis.model_json_schema()

    assert schema["additionalProperties"] is False
    assert schema["$defs"]["Customer"]["additionalProperties"] is False
    assert schema["$defs"]["Evidence"]["additionalProperties"] is False


def test_customer_analysis_schema_requires_all_properties_for_openai_response_format():
    schema = CustomerAnalysis.model_json_schema()

    assert set(schema["required"]) == set(schema["properties"])
    assert set(schema["$defs"]["Customer"]["required"]) == set(
        schema["$defs"]["Customer"]["properties"]
    )
    assert set(schema["$defs"]["Evidence"]["required"]) == set(
        schema["$defs"]["Evidence"]["properties"]
    )
