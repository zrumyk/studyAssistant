import pytest
from study.manual_grounding import ManualBasis,validate_manual_steps
from study.schemas import Plan


def manual_plan(plan):
    data=dict(plan)
    data['steps']=[dict(plan['steps'][0],kind='manual',title='External action')]
    return Plan.model_validate(data)


def test_optional_matlab_examples_do_not_establish_a_mandatory_manual_step(plan):
    class Model:
        def generate(self,prompt,schema):
            assert 'самі по собі НЕ вимагають' in prompt
            return ManualBasis(origin='sources',excerpt='',necessity='')
    with pytest.raises(ValueError,match='не має підтвердженої'):
        validate_manual_steps(manual_plan(plan),'Process images','Example: rgb2gray in MATLAB',Model())


def test_required_external_simulation_keeps_its_exact_source_basis(plan):
    quote='Create and run the circuit in Tinkercad.'
    class Model:
        def generate(self,*a):return ManualBasis(origin='sources',excerpt=quote,necessity='Real external simulation required')
    proof=validate_manual_steps(manual_plan(plan),'Perform lab',quote,Model())
    assert proof==[{'step_index':0,'origin':'sources','excerpt':quote,'necessity':'Real external simulation required'}]


def test_invented_quote_is_rejected_and_automatic_plan_needs_no_extra_request(plan):
    class Model:
        def generate(self,*a):return ManualBasis(origin='sources',excerpt='Invented mandatory software',necessity='Required')
    with pytest.raises(ValueError):validate_manual_steps(manual_plan(plan),'task','actual sources',Model())
    assert validate_manual_steps(Plan.model_validate(plan),'task','sources',Model())==[]
