import pytest
from study.visual_review import svg_text_findings,pdf_tail_findings


def svg(tmp_path,body):
    p=tmp_path/'fixture.svg';p.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 200" font-size="18">'+body+'</svg>');return p


def test_overlapping_center_labels_reported_without_changing_source(tmp_path):
    p=svg(tmp_path,'<text x="100" y="50" text-anchor="middle">alpha</text><text x="100" y="50" text-anchor="middle">beta</text>');before=p.read_bytes();result=svg_text_findings(p)
    assert result['finding_count']==1 and result['findings'][0]['kind']=='svg_text_overlap_candidate'
    assert p.read_bytes()==before


def test_separated_labels_not_flagged_and_font_inheritance_kept(tmp_path):
    p=svg(tmp_path,'<g font-size="12"><text x="10" y="30">alpha</text><text x="160" y="30">beta</text></g>')
    result=svg_text_findings(p);assert result['checked_texts']==2 and result['finding_count']==0


@pytest.mark.parametrize('body',[
    '<g transform="rotate(30)"><text x="10" y="30">rotated</text></g>',
    '<text x="10" y="30"><tspan dy="3">subscript</tspan></text>',
    '<text style="font-size:20px" x="10" y="30">styled</text>',
    '<text x="10 20" y="30">positions</text>',
])
def test_unsupported_typography_has_explicit_partial_coverage(tmp_path,body):
    result=svg_text_findings(svg(tmp_path,body))
    assert result['coverage']=='partial' and result['unhandled_texts']>0


def test_many_labels_have_bounded_diagnostics(tmp_path):
    result=svg_text_findings(svg(tmp_path,''.join('<text x="50" y="30">label</text>' for _ in range(220))))
    assert result['checked_texts']==200 and len(result['findings'])==25 and result['coverage']=='partial'


def test_dtd_not_parsed(tmp_path):
    p=tmp_path/'bad.svg';p.write_text('<!DOCTYPE svg><svg/>')
    assert svg_text_findings(p)['coverage']=='unknown'


def test_cover_and_short_non_code_tail_are_not_mistaken_for_source(monkeypatch):
    import pypdf
    class Page:
        def extract_text(self):return 'short conclusion\\n2'
    class Reader:
        def __init__(self,path):self.pages=[Page(),Page()]
    monkeypatch.setattr(pypdf,'PdfReader',Reader)
    assert not pdf_tail_findings('unused')['findings']


@pytest.mark.parametrize('tail', ['23\nsys.exit(main())','sys.exit(main())\n23'])
def test_footer_position_does_not_hide_orphan(monkeypatch,tail):
    import pypdf
    class Page:
        def __init__(self,text):self.text=text
        def extract_text(self):return self.text
    class Reader:
        def __init__(self,path):self.pages=[Page('Додаток Програмний код')]+[Page('many source lines')]*21+[Page(tail)]
    monkeypatch.setattr(pypdf,'PdfReader',Reader)
    result=pdf_tail_findings('unused')
    assert result['findings'][0]['kind']=='orphan_source_tail' and result['findings'][0]['lines']==['sys.exit(main())']
