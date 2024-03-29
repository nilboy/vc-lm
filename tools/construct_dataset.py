import fire
import glob
import math
import numpy as np
import os
from typing import List, Any
from tqdm.auto import tqdm
from joblib.parallel import Parallel, delayed

from encodec import EncodecModel
from encodec.utils import convert_audio

import torchaudio
import torch

from whisper.audio import log_mel_spectrogram

import webdataset as wds


def get_code_list(audio_list: List[str],
                  gpu_id: int = 0):
    device = f'cuda:{gpu_id}'
    # Instantiate a pretrained EnCodec model
    model = EncodecModel.encodec_model_24khz()
    model.set_target_bandwidth(6.0)
    model = model.cuda(device)
    code_list = []
    for audio in tqdm(audio_list, desc='calculate codes...'):
        code_list.append(get_code(audio, device, model))
    return code_list

def get_code(audio: str,
             device: str,
             model: Any):
    try:
        wav, sr = torchaudio.load(audio)
        wav = convert_audio(wav, sr, model.sample_rate, model.channels)
        wav = wav.unsqueeze(0)
        wav = wav.cuda(device)
        # Extract discrete codes from EnCodec
        with torch.no_grad():
            encoded_frames = model.encode(wav)
        code = torch.cat([encoded[0] for encoded in encoded_frames], dim=-1)  # [B, n_q, T]
        return code.cpu().numpy().astype(np.int16)[0]
    except:
        print(f'{audio} code error...')
        return None

def get_mel_spectrogram(audio):
    try:
        mel = log_mel_spectrogram(audio)
        return mel.numpy()
    except:
        print(f'{audio} mel error...')
        return None

def process_audios(audios: List[str],
                   num_workers: int):
    """
    处理audios文件
    Args:
        audios: List[str]
    Returns:
        records: List[Dict]
    """
    records = []
    # 计算mel
    mels = Parallel(n_jobs=num_workers)(delayed(get_mel_spectrogram)(audio) for audio in tqdm(audios, desc='calculate mels...'))
    # 计算code
    num_gpus = torch.cuda.device_count()
    per_gpu_samples = math.ceil(len(audios)/num_gpus)
    codes_list = Parallel(n_jobs=num_gpus)(delayed(get_code_list)(audios[i*per_gpu_samples:(i+1)*per_gpu_samples], i) \
                                      for i in range(0, num_gpus))
    codes = []
    for codes_item in codes_list:
        codes.extend(codes_item)
    for mel, code in zip(mels, codes):
        records.append({
            'mel': mel,
            'code': code
        })
    return records


def construct_dataset(input_dir,
                      output_dir,
                      partition_size=1000,
                      num_workers=10):
    os.makedirs(output_dir, exist_ok=True)
    input_files = glob.glob(f"{input_dir}/**/*.wav", recursive=True)

    with wds.ShardWriter(os.path.join(output_dir, 'shard-%06d.tar'),
                         maxcount=10000000, maxsize=1<<32) as sink:
        index = 0
        for partition_start in tqdm(range(0, len(input_files), partition_size)):
            audios = input_files[partition_start:partition_start+partition_size]
            records = process_audios(audios, num_workers=num_workers)
            for record in records:
                if record['mel'] is not None and record['code'] is not None:
                    sink.write({
                        '__key__': "%011d" % index,
                        'data.pyd': record})
                    index += 1
    print(f'records number: {index}')


if __name__ == '__main__':
    fire.Fire(construct_dataset)
