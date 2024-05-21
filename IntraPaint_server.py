"""
Starts the GLID-3-XL image inpainting server.
"""
from glid_3_xl.load_models import load_models
from util.arg_parser import build_arg_parser
from glid_3_xl.ml_utils import get_device
from colabFiles.server import start_server

# argument parsing:
parser = build_arg_parser(include_gen_params=False, include_edit_params=False)
parser.add_argument('--port', type=int, default=5555, required=False,
                    help='Port used when running in server mode.')
args = parser.parse_args()

device = get_device()
print('Using device:', device)

model_params, model, diffusion, ldm, bert, clip_model, clip_preprocess, normalize = load_models(device,
                                                                                                model_path=args.model_path,
                                                                                                bert_path=args.bert_path,
                                                                                                kl_path=args.kl_path,
                                                                                                steps=args.steps,
                                                                                                clip_guidance=args.clip_guidance,
                                                                                                cpu=args.cpu,
                                                                                                ddpm=args.ddpm,
                                                                                                ddim=args.ddim)
app = start_server(device, model_params, model, diffusion, ldm, bert, clip_model, clip_preprocess, normalize)
app.run(port=args.port, host='0.0.0.0')
