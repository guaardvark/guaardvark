class VADProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.energyThreshold = options.processorOptions?.energyThreshold || 0.02;
    this.smoothingWindow = options.processorOptions?.smoothingWindow || 5;
    this.volumeHistory = [];
    this.sampleRate = 16000; // Usually provided by the context
    this.frameCount = 0;
  }

  process(inputs, _outputs, _parameters) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    
    const channelData = input[0];
    if (!channelData) return true;

    // Calculate RMS volume for this block (usually 128 samples)
    let sumSquares = 0;
    for (let i = 0; i < channelData.length; i++) {
      sumSquares += channelData[i] * channelData[i];
    }
    const rms = Math.sqrt(sumSquares / channelData.length);
    
    // Smooth volume
    this.volumeHistory.push(rms);
    if (this.volumeHistory.length > this.smoothingWindow) {
      this.volumeHistory.shift();
    }
    
    const smoothedVolume = this.volumeHistory.reduce((a, b) => a + b, 0) / this.volumeHistory.length;
    
    // Send message to main thread every ~100ms (approx 12 blocks at 16kHz)
    this.frameCount++;
    if (this.frameCount % 12 === 0) {
      this.port.postMessage({
        volume: smoothedVolume,
        isSpeaking: smoothedVolume > this.energyThreshold
      });
    }
    
    return true;
  }
}

registerProcessor('vad-processor', VADProcessor);
