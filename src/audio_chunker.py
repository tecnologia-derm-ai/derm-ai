import os
import wave
import tempfile

MAX_SIZE_MB = 20


def dividir_audio(caminho_audio):
    tamanho_bytes = os.path.getsize(caminho_audio)
    tamanho_mb = tamanho_bytes / (1024 * 1024)

    # Se for pequeno, retorna o próprio arquivo
    if tamanho_mb <= MAX_SIZE_MB:
        return [caminho_audio]

    # Se for maior que o limite e for WAV, divide nativamente
    if caminho_audio.lower().endswith('.wav'):
        try:
            with wave.open(caminho_audio, 'rb') as wav_in:
                params = wav_in.getparams()
                num_channels = params.nchannels
                sample_width = params.sampwidth
                frame_rate = params.framerate
                num_frames = params.nframes
                
                # Estimar bytes por segundo de áudio
                bytes_por_segundo = num_channels * sample_width * frame_rate
                max_bytes_por_chunk = MAX_SIZE_MB * 1024 * 1024
                
                # Calcular duração aproximada do chunk em segundos
                segundos_por_chunk = int(max_bytes_por_chunk / bytes_por_segundo)
                if segundos_por_chunk < 30:
                    segundos_por_chunk = 30
                    
                frames_per_chunk = frame_rate * segundos_por_chunk
                
                chunk_paths = []
                chunk_index = 0
                
                base_dir = os.path.dirname(caminho_audio) or tempfile.gettempdir()
                base_name = os.path.splitext(os.path.basename(caminho_audio))[0]
                
                while wav_in.tell() < num_frames:
                    chunk_frames = wav_in.readframes(frames_per_chunk)
                    if not chunk_frames:
                        break
                    
                    chunk_filename = f"{base_name}_chunk_{chunk_index}.wav"
                    chunk_path = os.path.join(base_dir, chunk_filename)
                    
                    with wave.open(chunk_path, 'wb') as wav_out:
                        wav_out.setparams(params)
                        wav_out.writeframes(chunk_frames)
                    
                    chunk_paths.append(chunk_path)
                    chunk_index += 1
                    
            return chunk_paths
        except Exception as e:
            print(f"Erro ao fatiar áudio WAV: {e}")
            return [caminho_audio]

    # Se for outro formato (MP3, M4A, etc.), envia como está (Whisper aceita até 25MB)
    return [caminho_audio]


def transcrever_audio_grande(caminho_audio, transcrever_func, progress_bar=None):
    partes = dividir_audio(caminho_audio)
    texto_final = ""
    total = len(partes)

    try:
        for i, parte in enumerate(partes):
            texto = transcrever_func(parte)
            texto_final += "\n" + texto

            if progress_bar:
                progress_bar.progress((i + 1) / total)
    finally:
        # Limpar arquivos de chunks temporários criados
        if len(partes) > 1:
            for parte in partes:
                try:
                    if os.path.exists(parte):
                        os.remove(parte)
                except Exception as e:
                    print(f"Erro ao remover arquivo temporário {parte}: {e}")

    return texto_final.strip()