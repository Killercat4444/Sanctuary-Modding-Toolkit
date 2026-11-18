"""Small dependency-free GIF/PNG writers for cached effect previews."""
from pathlib import Path
import struct
import zlib
import numpy as np


def _lzw(indices):
    clear, end = 256, 257
    dictionary = {}
    next_code, width = 258, 9
    output = bytearray()
    bits = value = 0
    def emit(code, count):
        nonlocal bits, value
        value |= code << bits
        bits += count
        while bits >= 8:
            output.append(value & 255)
            value >>= 8
            bits -= 8
    emit(clear, width)
    prefix = int(indices[0])
    for byte in indices[1:]:
        byte = int(byte)
        key = (prefix, byte)
        if key in dictionary:
            prefix = dictionary[key]
            continue
        emit(prefix, width)
        # The decoder adds the previous entry one emitted code later.
        if next_code == (1 << width) and width < 12:
            width += 1
        if next_code < 4096:
            dictionary[key] = next_code
            next_code += 1
        else:
            emit(clear, width)
            dictionary.clear()
            next_code, width = 258, 9
        prefix = byte
    emit(prefix, width)
    if next_code == (1 << width) and width < 12:
        width += 1
    emit(end, width)
    if bits:
        output.append(value & 255)
    return bytes(output)


def write_gif(path, frames, fps=12):
    frames = np.asarray(frames, dtype=np.uint8)
    _, height, width, _ = frames.shape
    # Clip-wide weighted median-cut palette. A fixed 3/3/2 cube turns subtle
    # blue backgrounds olive and erases faint colored trails. No Pillow install
    # is required, and one palette across frames avoids animated color flicker.
    flat=frames[:,:,:,:3].reshape(-1,3).astype('uint16')>>3
    codes=(flat[:,0]<<10)|(flat[:,1]<<5)|flat[:,2]
    unique,inverse,counts=np.unique(codes,return_inverse=True,return_counts=True)
    colors=np.stack(((unique>>10)&31,(unique>>5)&31,unique&31),axis=1).astype('float32')*8+4
    boxes=[np.arange(len(unique))]
    while len(boxes)<min(256,len(unique)):
        scores=[float(np.ptp(colors[b],axis=0).max()*counts[b].sum()) if len(b)>1 else -1 for b in boxes]
        selected=int(np.argmax(scores))
        if scores[selected]<0:break
        box=boxes.pop(selected);axis=int(np.argmax(np.ptp(colors[box],axis=0)))
        ordered=box[np.argsort(colors[box,axis],kind='stable')]
        split=int(np.searchsorted(np.cumsum(counts[ordered]),counts[ordered].sum()/2))+1
        split=max(1,min(len(ordered)-1,split));boxes.extend((ordered[:split],ordered[split:]))
    palette_colors=np.array([np.average(colors[b],axis=0,weights=counts[b]) for b in boxes],dtype='float32')
    mapping=np.empty(len(unique),dtype='uint8')
    for offset in range(0,len(unique),512):
        distances=((colors[offset:offset+512,None,:]-palette_colors[None,:,:])**2).sum(axis=2)
        mapping[offset:offset+512]=distances.argmin(axis=1)
    indexed=mapping[inverse].reshape(len(frames),height,width)
    padded=np.zeros((256,3),dtype='uint8');padded[:len(palette_colors)]=np.clip(np.rint(palette_colors),0,255).astype('uint8')
    palette=padded.tobytes()
    result = bytearray(b'GIF89a' + struct.pack('<HHBBB', width, height, 0xf7, 0, 0) + palette)
    result += b'\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00'
    delay = max(1, round(100 / fps))
    previous_indices = previous_encoded = None
    for indices in indexed:
        result += b'\x21\xf9\x04\x04' + struct.pack('<H', delay) + b'\x00\x00'
        result += b'\x2c' + struct.pack('<HHHHB', 0, 0, width, height, 0)
        # Reuse identical consecutive frames without collapsing the animation.
        # Bytes iterate as integers already; output is identical to numpy input.
        raw_indices = indices.tobytes()
        encoded = previous_encoded if raw_indices == previous_indices else _lzw(raw_indices)
        previous_indices, previous_encoded = raw_indices, encoded
        result.append(8)
        for offset in range(0, len(encoded), 255):
            block = encoded[offset:offset + 255]
            result.append(len(block)); result += block
        result.append(0)
    result.append(0x3b)
    Path(path).write_bytes(result)


def write_png(path, pixels):
    pixels = np.asarray(pixels, dtype=np.uint8)
    height, width, _ = pixels.shape
    def chunk(name, data):
        return struct.pack('>I', len(data)) + name + data + struct.pack('>I', zlib.crc32(name + data) & 0xffffffff)
    raw = b''.join(b'\x00' + row.tobytes() for row in pixels)
    Path(path).write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
                           + chunk(b'IDAT', zlib.compress(raw, 3)) + chunk(b'IEND', b''))
